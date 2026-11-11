#include "llvm/Analysis/BranchProbabilityInfo.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/Analysis/LoopInfo.h"
#include "llvm/Analysis/BlockFrequencyInfo.h"
#include "llvm/Analysis/InlineCost.h"
#include "llvm/Analysis/AssumptionCache.h"
#include "llvm/Analysis/TargetLibraryInfo.h"
#include "llvm/Analysis/TargetTransformInfo.h"
#include "llvm/IR/CFG.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/MDBuilder.h"
#include "llvm/IR/Verifier.h"
#include "llvm/IRReader/IRReader.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/ProfileData/InstrProf.h"
#include "llvm/ProfileData/InstrProfReader.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/ModRef.h"
#include "llvm/Support/SourceMgr.h"
#include "llvm/Support/VirtualFileSystem.h"
#include "llvm/Support/xxhash.h"
#include "llvm/Transforms/Utils/BasicBlockUtils.h"
#include "llvm/Transforms/Utils/Local.h"
#include "llvm/Transforms/Utils/SampleProfileInference.h"
#include <cmath>

using namespace llvm;

static cl::opt<std::string> Action(cl::Positional, cl::Required);
static cl::opt<std::string> Input(cl::Positional, cl::Required);
static cl::opt<std::string> Output("output", cl::Required);
static cl::opt<std::string> GraphPath("graph");
static cl::opt<std::string> ProfilePath("profile");
static cl::opt<std::string> InstrumentedPath("instrumented");
static cl::opt<std::string> DecisionsPath("decisions");
static cl::opt<std::string> SelectionPath("selection");
static cl::opt<bool> RiskGate("risk-gate", cl::init(false));
static cl::opt<bool> DropBranchWeights("drop-branch-weights", cl::init(false));
static cl::opt<bool> ConsumerDecisions("consumer-decisions", cl::init(false));
static cl::opt<bool> NonlocalFlow("nonlocal-flow", cl::init(false));

static void fail(const Twine &Message) { report_fatal_error(Message); }

static json::Value readJSON(StringRef Path) {
  auto Buffer = MemoryBuffer::getFile(Path);
  if (!Buffer)
    fail("Cannot read " + Path);
  auto Parsed = json::parse((*Buffer)->getBuffer());
  if (!Parsed)
    fail(toString(Parsed.takeError()));
  return std::move(*Parsed);
}

static void writeJSON(StringRef Path, json::Value Value) {
  std::error_code Error;
  raw_fd_ostream Stream(Path, Error);
  if (Error)
    fail(Error.message());
  Stream << formatv("{0:2}\n", Value);
}

static void writeIR(StringRef Path, Module &ModuleValue) {
  if (verifyModule(ModuleValue, &errs()))
    fail("Invalid LLVM module");
  std::error_code Error;
  raw_fd_ostream Stream(Path, Error);
  if (Error)
    fail(Error.message());
  ModuleValue.print(Stream, nullptr);
}

static json::Array &functions(json::Value &Value) {
  auto *Object = Value.getAsObject();
  auto *Array = Object ? Object->getArray("functions") : nullptr;
  if (!Array)
    fail("Expected functions array");
  return *Array;
}

static int64_t integer(const json::Object &Object, StringRef Key) {
  auto Value = Object.getInteger(Key);
  if (!Value)
    fail("Missing integer: " + Key);
  return *Value;
}

static double number(const json::Value &Value) {
  auto Result = Value.getAsNumber();
  if (!Result || !std::isfinite(*Result) || *Result < 0)
    fail("Expected finite nonnegative count");
  return *Result;
}

static void checkCanonicalIR(const json::Value &Graph) {
  auto Buffer = MemoryBuffer::getFile(Input);
  if (!Buffer || std::to_string(xxh3_64bits((*Buffer)->getBuffer())) != *Graph.getAsObject()->getString("ir_id"))
    fail("Canonical IR changed since graph extraction");
}

static void invalidateMemoryEffects(Module &ModuleValue) {
  // 插桩增加全局计数器写入。清除优化前推导的内存属性，防止递归调用
  // 被当作纯函数，导致计数器更新被移动、合并或删除。
  for (Function &FunctionValue : ModuleValue) {
    if (FunctionValue.isDeclaration())
      continue;
    FunctionValue.setMemoryEffects(MemoryEffects::unknown());
    FunctionValue.removeFnAttr(Attribute::Speculatable);
    for (BasicBlock &Block : FunctionValue)
      for (Instruction &InstructionValue : Block)
        if (auto *Call = dyn_cast<CallBase>(&InstructionValue)) {
          const Function *Callee = Call->getCalledFunction();
          if (!Callee || !Callee->isDeclaration()) {
            Call->setMemoryEffects(MemoryEffects::unknown());
            Call->removeFnAttr(Attribute::Speculatable);
          }
        }
  }
}

// 每个基本块插一个计数器。先拆开关键边，之后每条边的次数都能直接从
// 起点或终点计数得到，避免把 LLVM 的生成树计数器误认为基本块计数。
static void prepare(Module &ModuleValue) {
  json::Array FunctionGraphs;
  for (Function &FunctionValue : ModuleValue) {
    if (FunctionValue.isDeclaration())
      continue;
    removeUnreachableBlocks(FunctionValue);
    DenseMap<BasicBlock *, BasicBlock *> CallContinuations;
    DenseSet<BasicBlock *> ReturnsTwiceBlocks;
    if (NonlocalFlow) {
      SmallVector<CallInst *, 32> Calls;
      for (BasicBlock &Block : FunctionValue)
        for (Instruction &I : Block)
          if (auto *Call = dyn_cast<CallInst>(&I); Call && !Call->isMustTailCall() &&
              !(Call->getCalledFunction() && Call->getCalledFunction()->isIntrinsic()))
            Calls.push_back(Call);
      for (CallInst *Call : Calls) {
        BasicBlock *Before = Call->getParent();
        BasicBlock *After = SplitBlock(Before, Call->getNextNode());
        CallContinuations[Before] = After;
        if (Call->hasFnAttr(Attribute::ReturnsTwice)) ReturnsTwiceBlocks.insert(Before);
      }
    }
    for (BasicBlock &Block : FunctionValue) {
      Instruction *Terminator = Block.getTerminator();
      if (!isa<CondBrInst>(Terminator) && !isa<UncondBrInst>(Terminator) && !isa<SwitchInst>(Terminator) &&
          !isa<ReturnInst>(Terminator) && !isa<UnreachableInst>(Terminator) &&
          !isa<InvokeInst>(Terminator) && !isa<ResumeInst>(Terminator))
        fail("Unsupported terminator in " + FunctionValue.getName());
    }
    SplitAllCriticalEdges(FunctionValue);
    DenseMap<BasicBlock *, unsigned> BlockIDs;
    unsigned NextID = 0;
    for (BasicBlock &Block : FunctionValue) {
      BlockIDs[&Block] = NextID;
      Block.setName("draper.bb." + Twine(NextID++));
    }
    DominatorTree Dominators(FunctionValue);
    LoopInfo Loops(Dominators);
    json::Array Blocks;
    json::Array Edges;
    Edges.push_back(json::Object{{"src", -1}, {"dst", 0}, {"counter", 0}, {"slot", -1}});
    std::string Signature;
    raw_string_ostream SignatureStream(Signature);
    SignatureStream << FunctionValue.getName() << ':';
    for (BasicBlock &Block : FunctionValue) {
      unsigned BlockID = BlockIDs[&Block];
      json::Array Calls;
      json::Object Opcodes;
      json::Array Locations;
      for (Instruction &InstructionValue : Block) {
        StringRef Opcode = InstructionValue.getOpcodeName();
        Opcodes[Opcode] = Opcodes.getInteger(Opcode).value_or(0) + 1;
        // 保存指令来源和内联调用位置，供跨阶段候选匹配使用。
        // 同一源码位置可对应多个块，后续匹配保留这种多对多关系。
        if (const DILocation *Location = InstructionValue.getDebugLoc().get()) {
          json::Array InlineStack;
          for (const DILocation *Caller = Location->getInlinedAt(); Caller;
               Caller = Caller->getInlinedAt())
            InlineStack.push_back(json::Object{{"file", Caller->getFilename()},
                                              {"line", Caller->getLine()},
                                              {"column", Caller->getColumn()}});
          Locations.push_back(json::Object{{"file", Location->getFilename()},
                                           {"line", Location->getLine()},
                                           {"column", Location->getColumn()},
                                           {"inlined_at", std::move(InlineStack)}});
        }
        if (auto *Call = dyn_cast<CallBase>(&InstructionValue)) {
          if (auto *Callee = Call->getCalledFunction(); Callee && !Callee->isIntrinsic())
            Calls.push_back(Callee->getName());
        }
      }
      json::Array SuccessorEdges;
      Instruction *Terminator = Block.getTerminator();
      for (unsigned Slot = 0; Slot < Terminator->getNumSuccessors(); ++Slot) {
        BasicBlock *Target = Terminator->getSuccessor(Slot);
        unsigned Counter;
        int Subtract = -1;
        if (auto *Invoke = dyn_cast<InvokeInst>(Terminator); Invoke && Slot == 1) {
          if (Invoke->getNormalDest()->getSinglePredecessor() != &Block)
            fail("Invoke normal edge was not split");
          Counter = BlockID;
          Subtract = BlockIDs[Invoke->getNormalDest()];
        } else if (Terminator->getNumSuccessors() == 1)
          Counter = CallContinuations.contains(&Block) ? BlockIDs[Target] : BlockID;
        else if (Target->getSinglePredecessor() == &Block)
          Counter = BlockIDs[Target];
        else
          fail("Unsplit or duplicate critical edge in " + FunctionValue.getName());
        SuccessorEdges.push_back(static_cast<int64_t>(Edges.size()));
        Edges.push_back(json::Object{{"src", BlockID}, {"dst", BlockIDs[Target]},
                                    {"counter", Counter}, {"counter_subtract", Subtract},
                                    {"counter_min", CallContinuations.contains(&Block) ? int64_t(BlockID) : -1}, {"slot", Slot}});
        SignatureStream << BlockID << '>' << BlockIDs[Target] << '/' << Slot << ';';
      }
      if (Terminator->getNumSuccessors() == 0)
        Edges.push_back(json::Object{{"src", BlockID}, {"dst", -1},
                                    {"counter", BlockID}, {"slot", -1}});
      if (auto It = CallContinuations.find(&Block); It != CallContinuations.end()) {
        unsigned After = BlockIDs[It->second];
        Edges.push_back(json::Object{{"src", BlockID}, {"dst", -1}, {"counter", BlockID},
            {"counter_subtract", After}, {"positive_part", true}, {"nonlocal", true}, {"slot", -1}});
        if (ReturnsTwiceBlocks.contains(&Block))
          Edges.push_back(json::Object{{"src", -1}, {"dst", After}, {"counter", After},
              {"counter_subtract", BlockID}, {"positive_part", true}, {"nonlocal", true}, {"slot", -1}});
        SignatureStream << "nonlocal:" << BlockID << '>' << After << ';';
      }
      Blocks.push_back(json::Object{{"id", BlockID}, {"name", Block.getName()},
                                   {"instructions", static_cast<int64_t>(Block.size())},
                                   {"opcodes", std::move(Opcodes)},
                                   {"source_locations", std::move(Locations)},
                                   {"loop_depth", Loops.getLoopDepth(&Block)},
                                   {"loop_header", Loops.getLoopFor(&Block) ? int64_t(BlockIDs[Loops.getLoopFor(&Block)->getHeader()]) : -1},
                                   {"calls", std::move(Calls)},
                                   {"successor_edges", std::move(SuccessorEdges)}});
    }
    FunctionGraphs.push_back(json::Object{
        {"name", FunctionValue.getName()}, {"profile_name", "draper." + FunctionValue.getName().str()},
        {"source_file", FunctionValue.getSubprogram() ? FunctionValue.getSubprogram()->getFilename() : StringRef()},
        {"signature", std::to_string(xxh3_64bits(StringRef(Signature)))},
        {"closed_calls", FunctionValue.hasLocalLinkage() && !FunctionValue.hasAddressTaken()},
        {"blocks", std::move(Blocks)}, {"edges", std::move(Edges)}});
  }
  // 同一个规范化 IR 分别用于插桩、无插桩编译和恢复结果回写。
  // 图中计数来自这个位置，不能与任意 O0/O3 阶段的块编号混用。
  std::string CanonicalText;
  raw_string_ostream CanonicalStream(CanonicalText);
  ModuleValue.print(CanonicalStream, nullptr);
  std::string IRID = std::to_string(xxh3_64bits(StringRef(CanonicalText)));
  json::Value Graph(json::Object{{"schema", 1}, {"ir_id", IRID},
                                {"stage", "early IR after critical-edge splitting"},
                                {"functions", std::move(FunctionGraphs)}});
  writeIR(Output, ModuleValue);
  invalidateMemoryEffects(ModuleValue);
  for (json::Value &Value : functions(Graph)) {
    json::Object &Description = *Value.getAsObject();
    std::string CombinedID = IRID + ":" + Description.getString("signature")->str();
    Description["signature"] = std::to_string(xxh3_64bits(StringRef(CombinedID)));
    Function &FunctionValue = *ModuleValue.getFunction(*Description.getString("name"));
    std::string ProfileName = Description.getString("profile_name")->str();
    GlobalVariable *NameVariable = createPGOFuncNameVar(FunctionValue, ProfileName);
    uint64_t Signature = std::stoull(Description.getString("signature")->str());
    unsigned Counter = 0;
    for (BasicBlock &Block : FunctionValue) {
      IRBuilder<> Builder(&*Block.getFirstInsertionPt());
      Builder.CreateIntrinsic(Intrinsic::instrprof_increment, {},
                              {NameVariable, Builder.getInt64(Signature),
                               Builder.getInt32(FunctionValue.size()), Builder.getInt32(Counter++)});
    }
  }
  writeIR(InstrumentedPath, ModuleValue);
  writeJSON(GraphPath, std::move(Graph));
}

static void truth(json::Value Graph) {
  auto Reader = IndexedInstrProfReader::create(ProfilePath, *vfs::getRealFileSystem());
  if (!Reader)
    fail(toString(Reader.takeError()));
  json::Array Profiles;
  for (json::Value &Value : functions(Graph)) {
    json::Object &Description = *Value.getAsObject();
    auto Record = (*Reader)->getInstrProfRecord(*Description.getString("profile_name"),
                                              std::stoull(Description.getString("signature")->str()));
    if (!Record)
      fail(toString(Record.takeError()));
    if (Record->Counts.size() != Description.getArray("blocks")->size())
      fail("Counter count does not match CFG");
    json::Array Blocks;
    json::Array Edges;
    for (uint64_t Count : Record->Counts)
      Blocks.push_back(Count);
    for (const json::Value &Edge : *Description.getArray("edges")) {
      auto &E = *Edge.getAsObject();
      uint64_t Count = Record->Counts.at(integer(E, "counter"));
      int64_t Minimum = E.getInteger("counter_min").value_or(-1);
      if (Minimum >= 0) Count = std::min(Count, Record->Counts.at(Minimum));
      int64_t Subtract = E.getInteger("counter_subtract").value_or(-1);
      if (Subtract >= 0) {
        uint64_t Normal = Record->Counts.at(Subtract);
        if (E.getBoolean("positive_part").value_or(false)) Count = Count > Normal ? Count-Normal : 0;
        else {
          if (Normal > Count) fail("Exception edge has negative count");
          Count -= Normal;
        }
      }
      Edges.push_back(Count);
    }
    Profiles.push_back(json::Object{{"name", *Description.getString("name")},
                                    {"blocks", std::move(Blocks)}, {"edges", std::move(Edges)}});
  }
  writeJSON(Output, json::Object{{"ir_id", *Graph.getAsObject()->getString("ir_id")},
                                {"kind", "exact offline block instrumentation"},
                                {"functions", std::move(Profiles)}});
}

// 校准只对选中的位置插桩。运行后按相同位置表读取计数，求解器不会
// 获得未选中位置的参考答案。
static void calibrate(Module *ModuleValue, json::Value Graph) {
  if (ModuleValue)
    invalidateMemoryEffects(*ModuleValue);
  json::Value Selection = readJSON(SelectionPath);
  auto *Selected = Selection.getAsObject()->getArray("blocks");
  if (!Selected || Selection.getAsObject()->getString("ir_id") != Graph.getAsObject()->getString("ir_id"))
    fail("Invalid calibration selection");
  std::vector<int64_t> Positions;
  for (const auto &Value : *Selected) {
    auto Position = Value.getAsInteger();
    if (!Position || *Position < 0 || llvm::is_contained(Positions, *Position))
      fail("Invalid or repeated calibration block");
    Positions.push_back(*Position);
  }
  std::unique_ptr<IndexedInstrProfReader> Reader;
  if (ModuleValue) {
    checkCanonicalIR(Graph);
  } else {
    auto Result = IndexedInstrProfReader::create(ProfilePath, *vfs::getRealFileSystem());
    if (!Result)
      fail(toString(Result.takeError()));
    Reader = std::move(*Result);
  }
  json::Array Anchors;
  int64_t BlockOffset = 0;
  for (const auto &Value : functions(Graph)) {
    const auto &Description = *Value.getAsObject();
    int64_t BlockCount = Description.getArray("blocks")->size();
    std::vector<int64_t> Local;
    for (int64_t Position : Positions)
      if (Position >= BlockOffset && Position < BlockOffset + BlockCount)
        Local.push_back(Position - BlockOffset);
    if (!Local.empty()) {
      std::string Name = "draper.anchor." + Description.getString("name")->str();
      std::string SelectionID = Description.getString("signature")->str();
      for (int64_t BlockID : Local)
        SelectionID += ":" + std::to_string(BlockID);
      uint64_t Signature = xxh3_64bits(StringRef(SelectionID));
      if (ModuleValue) {
        Function &FunctionValue = *ModuleValue->getFunction(*Description.getString("name"));
        auto *NameVariable = createPGOFuncNameVar(FunctionValue, Name);
        int64_t BlockID = 0;
        for (BasicBlock &Block : FunctionValue) {
          auto Position = llvm::find(Local, BlockID++);
          if (Position == Local.end())
            continue;
          IRBuilder<> Builder(&*Block.getFirstInsertionPt());
          Builder.CreateIntrinsic(Intrinsic::instrprof_increment, {},
                                  {NameVariable, Builder.getInt64(Signature), Builder.getInt32(Local.size()),
                                   Builder.getInt32(Position - Local.begin())});
        }
      } else {
        auto Record = Reader->getInstrProfRecord(Name, Signature);
        if (!Record)
          fail(toString(Record.takeError()));
        if (Record->Counts.size() != Local.size())
          fail("Calibration counter count mismatch");
        for (unsigned Index = 0; Index < Local.size(); ++Index)
          Anchors.push_back(json::Object{{"block", BlockOffset + Local[Index]}, {"count", Record->Counts[Index]}});
      }
    }
    BlockOffset += BlockCount;
  }
  if (llvm::any_of(Positions, [&](int64_t Position) { return Position >= BlockOffset; }))
    fail("Calibration block outside graph");
  if (ModuleValue)
    writeIR(Output, *ModuleValue);
  else
    writeJSON(Output, json::Object{{"kind", "measured sparse block instrumentation"}, {"anchors", std::move(Anchors)}});
}

// 直接调用当前 LLVM 的 profi 实现。缺失观测和观测为零分开表示。
static void profi(json::Value Graph) {
  json::Value Observations = readJSON(ProfilePath);
  if (Graph.getAsObject()->getString("ir_id") != Observations.getAsObject()->getString("ir_id"))
    fail("Observation/CFG mismatch");
  auto &ObservedFunctions = functions(Observations);
  if (ObservedFunctions.size() != functions(Graph).size())
    fail("Function count mismatch");
  json::Array Profiles;
  unsigned FunctionIndex = 0;
  for (json::Value &Value : functions(Graph)) {
    auto &Description = *Value.getAsObject();
    auto &Observed = *ObservedFunctions[FunctionIndex++].getAsObject();
    if (Description.getString("name") != Observed.getString("name"))
      fail("Function order mismatch");
    const auto &Samples = *Observed.getArray("samples");
    const auto &Present = *Observed.getArray("present");
    double Rate = *Observations.getAsObject()->getNumber("rate");
    if (!(Rate > 0 && Rate <= 1) || Samples.size() != Description.getArray("blocks")->size() ||
        Present.size() != Samples.size())
      fail("Invalid observations");
    FlowFunction Flow;
    Flow.Blocks.resize(Samples.size());
    bool AnyPositive = false;
    for (unsigned BlockID = 0; BlockID < Samples.size(); ++BlockID) {
      Flow.Blocks[BlockID].Index = BlockID;
      Flow.Blocks[BlockID].HasUnknownWeight = !Present[BlockID].getAsBoolean().value_or(false);
      Flow.Blocks[BlockID].Weight = std::llround(number(Samples[BlockID]) / Rate);
      AnyPositive |= !Flow.Blocks[BlockID].HasUnknownWeight && Flow.Blocks[BlockID].Weight > 0;
    }
    for (const auto &Edge : *Description.getArray("edges")) {
      int64_t Source = integer(*Edge.getAsObject(), "src");
      int64_t Target = integer(*Edge.getAsObject(), "dst");
      if (Source >= 0 && Target >= 0) {
        FlowJump Jump;
        Jump.Source = Source;
        Jump.Target = Target;
        Flow.Jumps.push_back(Jump);
      }
    }
    // 所有 vector 扩容结束后才保存元素地址，避免悬空指针。
    for (auto &Jump : Flow.Jumps) {
      Flow.Blocks[Jump.Source].SuccJumps.push_back(&Jump);
      Flow.Blocks[Jump.Target].PredJumps.push_back(&Jump);
    }
    if (AnyPositive && Flow.Blocks.size() > 1)
      applyFlowInference(Flow);
    else if (Flow.Blocks.size() == 1 && AnyPositive)
      Flow.Blocks[0].Flow = Flow.Blocks[0].Weight;
    json::Array Blocks;
    json::Array Edges;
    for (auto &Block : Flow.Blocks)
      Blocks.push_back(Block.Flow);
    unsigned JumpIndex = 0;
    for (const auto &Edge : *Description.getArray("edges")) {
      int64_t Source = integer(*Edge.getAsObject(), "src");
      int64_t Target = integer(*Edge.getAsObject(), "dst");
      if (Edge.getAsObject()->getBoolean("nonlocal").value_or(false)) { Edges.push_back(0); continue; }
      Edges.push_back(Source < 0 ? Flow.Blocks[Target].Flow
                                : Target < 0 ? Flow.Blocks[Source].Flow : Flow.Jumps[JumpIndex++].Flow);
    }
    Profiles.push_back(json::Object{{"name", *Description.getString("name")},
                                    {"blocks", std::move(Blocks)}, {"edges", std::move(Edges)}});
  }
  writeJSON(Output, json::Object{{"ir_id", *Graph.getAsObject()->getString("ir_id")},
                                {"kind", "LLVM applyFlowInference"}, {"functions", std::move(Profiles)}});
}

static void annotate(Module &ModuleValue, json::Value Graph) {
  checkCanonicalIR(Graph);
  json::Value Profile = readJSON(ProfilePath);
  if (Profile.getAsObject()->getString("ir_id") != Graph.getAsObject()->getString("ir_id"))
    fail("Profile/CFG mismatch");
  if (functions(Profile).size() != functions(Graph).size())
    fail("Profile function count mismatch");
  unsigned FunctionIndex = 0;
  for (json::Value &Value : functions(Graph)) {
    auto &Description = *Value.getAsObject();
    auto &Counts = *functions(Profile)[FunctionIndex++].getAsObject();
    if (Description.getString("name") != Counts.getString("name"))
      fail("Profile function order mismatch");
    Function &FunctionValue = *ModuleValue.getFunction(*Description.getString("name"));
    auto &Edges = *Counts.getArray("edges");
    if (Edges.size() != Description.getArray("edges")->size())
      fail("Profile edge count mismatch");
    FunctionValue.setEntryCount(std::llround(number(Edges[0])));
    unsigned BlockID = 0;
    for (BasicBlock &Block : FunctionValue) {
      auto &BlockDescription = *(*Description.getArray("blocks"))[BlockID].getAsObject();
      auto &Successors = *BlockDescription.getArray("successor_edges");
      Instruction *Terminator = Block.getTerminator();
      if (Successors.size() != Terminator->getNumSuccessors())
        fail("CFG successor mismatch");
      bool KeepWeights = !DropBranchWeights;
      if (RiskGate) {
        auto *Tags = Counts.getArray("block_tags");
        if (!Tags || Tags->size() != FunctionValue.size())
          fail("Risk gating requires block tags");
        auto Tag = (*Tags)[BlockID].getAsString();
        KeepWeights = KeepWeights && Tag && *Tag == "stable";
      }
      Terminator->setMetadata(LLVMContext::MD_prof, nullptr);
      if (Successors.size() > 1 && KeepWeights) {
        double Maximum = 0;
        for (const auto &Index : Successors)
          Maximum = std::max(Maximum, number(Edges[*Index.getAsInteger()]));
        if (Maximum > 0) {
          SmallVector<uint32_t> Weights;
          double Scale = std::min(1.0, (double(UINT32_MAX) / 2) / Maximum);
          for (const auto &Index : Successors)
            Weights.push_back(std::max(1LL, std::llround(number(Edges[*Index.getAsInteger()]) * Scale)));
          Terminator->setMetadata(LLVMContext::MD_prof, MDBuilder(ModuleValue.getContext()).createBranchWeights(Weights));
        }
      }
      ++BlockID;
    }
  }
  writeIR(Output, ModuleValue);
  // 让 LLVM 本身读取 !prof，再记录 isEdgeHot 的实际结果。
  // 这样可以发现计数缩放或元数据取整造成的判断变化。
  PassBuilder Builder;
  FunctionAnalysisManager Analyses;
  Builder.registerFunctionAnalyses(Analyses);
  json::Array Decisions;
  json::Array Consumers;
  for (Function &FunctionValue : ModuleValue) {
    if (FunctionValue.isDeclaration())
      continue;
    auto &Probabilities = Analyses.getResult<BranchProbabilityAnalysis>(FunctionValue);
    if (ConsumerDecisions) {
      auto &Frequencies = Analyses.getResult<BlockFrequencyAnalysis>(FunctionValue);
      auto Params = getInlineParamsFromOptLevel(3);
      Params.ComputeFullInlineCost = true;
      for (BasicBlock &Block : FunctionValue) {
        double Relative = double(Frequencies.getBlockFreq(&Block).getFrequency()) /
                          std::max(uint64_t(1), Frequencies.getEntryFreq().getFrequency());
        Consumers.push_back(json::Object{{"kind", "block-frequency"}, {"function", FunctionValue.getName()},
            {"block", Block.getName()}, {"relative_frequency", Relative}});
        unsigned CallIndex = 0;
        for (Instruction &Inst : Block) {
          auto *Call = dyn_cast<CallBase>(&Inst);
          if (!Call) continue;
          unsigned Index = CallIndex++;
          Function *Callee = Call->getCalledFunction();
          if (!Callee || Callee->isDeclaration() || Callee->isIntrinsic()) continue;
          auto Cost = getInlineCost(*Call, Params, Analyses.getResult<TargetIRAnalysis>(*Callee),
              [&](Function &F) -> AssumptionCache & { return Analyses.getResult<AssumptionAnalysis>(F); },
              [&](Function &F) -> const TargetLibraryInfo & { return Analyses.getResult<TargetLibraryAnalysis>(F); },
              [&](Function &F) -> BlockFrequencyInfo & { return Analyses.getResult<BlockFrequencyAnalysis>(F); });
          json::Object Entry{{"kind", "inline"}, {"function", FunctionValue.getName()},
              {"block", Block.getName()}, {"call_index", Index}, {"callee", Callee->getName()},
              {"decision", bool(Cost)}, {"mandatory", !Cost.isVariable()},
              {"callee_instructions", int64_t(Callee->getInstructionCount())}};
          if (Cost.isVariable()) {
            Entry["cost"] = Cost.getCost();
            Entry["threshold"] = Cost.getThreshold();
          }
          Consumers.push_back(std::move(Entry));
        }
      }
    }
    for (BasicBlock &Block : FunctionValue) {
      auto *Terminator = Block.getTerminator();
      if (Terminator->getNumSuccessors() < 2)
        continue;
      for (unsigned Slot = 0; Slot < Terminator->getNumSuccessors(); ++Slot) {
        auto Probability = Probabilities.getEdgeProbability(&Block, Slot);
        Decisions.push_back(json::Object{
            {"function", FunctionValue.getName()}, {"block", Block.getName()}, {"slot", Slot},
            {"probability", double(Probability.getNumerator()) / Probability.getDenominator()},
            {"hot", Probabilities.isEdgeHot(&Block, Terminator->getSuccessor(Slot))},
            {"has_profile_weights", Terminator->getMetadata(LLVMContext::MD_prof) != nullptr}});
      }
    }
  }
  if (!DecisionsPath.empty())
    writeJSON(DecisionsPath, json::Object{{"predicate", "LLVM BranchProbabilityInfo::isEdgeHot (>80%)"},
                                        {"decisions", std::move(Decisions)},
                                        {"consumer_decisions", std::move(Consumers)}});
}

static void prepareProbeCounters(Module &M) {
  invalidateMemoryEffects(M);
  json::Array Descriptions;
  for (Function &F : M) {
    SmallVector<IntrinsicInst *, 32> Probes;
    for (BasicBlock &B : F)
      for (Instruction &I : B)
        if (auto *P = dyn_cast<IntrinsicInst>(&I); P && P->getIntrinsicID() == Intrinsic::pseudoprobe)
          Probes.push_back(P);
    if (Probes.empty()) continue;
    std::string Signature = F.getName().str();
    json::Array Positions;
    for (auto *P : Probes) {
      uint64_t Guid = cast<ConstantInt>(P->getArgOperand(0))->getZExtValue();
      uint64_t Index = cast<ConstantInt>(P->getArgOperand(1))->getZExtValue();
      Signature += ':' + std::to_string(Guid) + ':' + std::to_string(Index);
      Positions.push_back(json::Object{{"block", P->getParent()->getName()},
          {"guid", std::to_string(Guid)}, {"probe", Index}});
    }
    uint64_t ID = xxh3_64bits(StringRef(Signature));
    std::string Name = "draper.probes." + F.getName().str();
    auto *NameVariable = createPGOFuncNameVar(F, Name);
    for (unsigned I = 0; I < Probes.size(); ++I) {
      IRBuilder<> Builder(Probes[I]);
      Builder.CreateIntrinsic(Intrinsic::instrprof_increment, {},
          {NameVariable, Builder.getInt64(ID), Builder.getInt32(Probes.size()), Builder.getInt32(I)});
    }
    Descriptions.push_back(json::Object{{"name", F.getName()}, {"profile_name", Name},
        {"signature", std::to_string(ID)}, {"probes", std::move(Positions)}});
  }
  writeIR(Output,M);
  writeJSON(GraphPath,json::Object{{"kind", "post-optimization probe occurrence counters"}, {"functions",std::move(Descriptions)}});
}

static void readProbeCounters(json::Value Graph) {
  auto Reader=IndexedInstrProfReader::create(ProfilePath,*vfs::getRealFileSystem());
  if (!Reader) fail(toString(Reader.takeError()));
  json::Array Rows;
  for (auto &Value : functions(Graph)) {
    auto &F=*Value.getAsObject();
    auto Record=(*Reader)->getInstrProfRecord(*F.getString("profile_name"),std::stoull(F.getString("signature")->str()));
    if (!Record) fail(toString(Record.takeError()));
    auto &Positions=*F.getArray("probes");
    if (Positions.size()!=Record->Counts.size()) fail("Probe occurrence count mismatch");
    for (unsigned I=0;I<Positions.size();++I) {
      auto &P=*Positions[I].getAsObject();
      Rows.push_back(json::Object{{"function",*F.getString("name")},{"block",*P.getString("block")},
        {"guid",*P.getString("guid")},{"probe",*P.getInteger("probe")},{"occurrence",I},{"count",Record->Counts[I]}});
    }
  }
  writeJSON(Output,json::Object{{"occurrences",std::move(Rows)}});
}

int main(int Argc, char **Argv) {
  cl::ParseCommandLineOptions(Argc, Argv);
  if (Action == "truth") {
    truth(readJSON(GraphPath));
  } else if (Action == "profi") {
    profi(readJSON(GraphPath));
  } else if (Action == "read-anchors") {
    calibrate(nullptr, readJSON(GraphPath));
  } else if (Action == "probe-truth") {
    readProbeCounters(readJSON(GraphPath));
  } else {
    LLVMContext Context;
    SMDiagnostic Diagnostic;
    auto ModuleValue = parseIRFile(Input, Diagnostic, Context);
    if (!ModuleValue) {
      Diagnostic.print(Argv[0], errs());
      return 1;
    }
    if (Action == "prepare")
      prepare(*ModuleValue);
    else if (Action == "probe-prepare")
      prepareProbeCounters(*ModuleValue);
    else if (Action == "annotate")
      annotate(*ModuleValue, readJSON(GraphPath));
    else if (Action == "calibrate")
      calibrate(ModuleValue.get(), readJSON(GraphPath));
    else
      fail("Expected prepare, truth, profi or annotate");
  }
  return 0;
}
