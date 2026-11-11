# 公开工作负载全块数据索引

共 80 个工作负载，其中 49 个应用工作负载来自 43 个应用家族，另有 31 个循环内核。

此索引表示两次精确计数及其模拟稀疏观测已完成；新算法性能结果另见 ../public-results-v1/results.md。
现代三个应用另有独立性能评价输入。原 40 项的旧算法性能已查看，aha 已用于区间校准，相关字段保留在 JSON 中。

| 程序 | 来源 | 集合 | 编译源码行数 | 函数 | 基本块 |
|---|---|---|---:|---:|---:|
| network-dijkstra | LLVM MultiSource | development | 175 | 6 | 52 |
| telecomm-CRC32 | LLVM MultiSource | validation | 192 | 5 | 20 |
| security-sha | LLVM MultiSource | test | 246 | 8 | 50 |
| viterbi | LLVM MultiSource | validation | 297 | 15 | 124 |
| automotive-basicmath | LLVM MultiSource | development | 316 | 5 | 65 |
| aha | LLVM MultiSource | validation | 491 | 32 | 176 |
| telecomm-fft | LLVM MultiSource | development | 527 | 12 | 116 |
| network-patricia | LLVM MultiSource | validation | 543 | 7 | 140 |
| anagram | LLVM MultiSource | test | 657 | 18 | 225 |
| automotive-bitcount | LLVM MultiSource | development | 671 | 22 | 76 |
| ks | LLVM MultiSource | development | 685 | 14 | 197 |
| minisat | LLVM MultiSource | test | 1,066 | 198 | 1,043 |
| security-rijndael | LLVM MultiSource | development | 1,142 | 7 | 150 |
| sgefa | LLVM MultiSource | development | 1,185 | 20 | 558 |
| ft | LLVM MultiSource | development | 1,265 | 37 | 241 |
| hexxagon | LLVM MultiSource | development | 1,563 | 76 | 712 |
| automotive-susan | LLVM MultiSource | development | 2,125 | 21 | 710 |
| hbd | LLVM MultiSource | development | 2,271 | 86 | 1,491 |
| lambda | LLVM MultiSource | test | 2,454 | 166 | 1,892 |
| yacr2 | LLVM MultiSource | development | 3,217 | 58 | 1,037 |
| Obsequi | LLVM MultiSource | development | 3,582 | 83 | 1,205 |
| SIBsim4 | LLVM MultiSource | development | 3,821 | 68 | 1,760 |
| spiff | LLVM MultiSource | development | 4,749 | 99 | 1,197 |
| telecomm-gsm | LLVM MultiSource | test | 5,477 | 92 | 917 |
| alacconvert-encode | LLVM MultiSource | development | 6,061 | 117 | 1,257 |
| alacconvert-decode | LLVM MultiSource | development | 6,062 | 117 | 1,257 |
| bc | LLVM MultiSource | test | 6,627 | 102 | 1,740 |
| siod | LLVM MultiSource | development | 7,323 | 496 | 4,072 |
| make_dparser | LLVM MultiSource | development | 12,329 | 321 | 5,134 |
| lua | LLVM MultiSource | test | 13,413 | 718 | 8,622 |
| consumer-lame | LLVM MultiSource | development | 19,298 | 235 | 4,097 |
| consumer-jpeg | LLVM MultiSource | development | 22,960 | 380 | 5,412 |
| ldecod | LLVM MultiSource | development | 28,699 | 442 | 10,047 |
| consumer-typeset | LLVM MultiSource | validation | 38,919 | 454 | 27,737 |
| kc | LLVM MultiSource | validation | 56,084 | 5,662 | 22,892 |
| lencod | LLVM MultiSource | development | 57,035 | 832 | 21,913 |
| oggenc | LLVM MultiSource | development | 58,356 | 430 | 6,548 |
| clamscan | LLVM MultiSource | development | 66,762 | 964 | 26,613 |
| SPASS | LLVM MultiSource | validation | 69,104 | 4,313 | 23,831 |
| sqlite3 | LLVM MultiSource | development | 86,632 | 1,085 | 18,282 |
| floyd-warshall | PolyBench | test | 172 | 16 | 80 |
| seidel-2d | PolyBench | test | 173 | 16 | 66 |
| jacobi-1d | PolyBench | development | 178 | 16 | 57 |
| jacobi-2d | PolyBench | development | 187 | 16 | 78 |
| trisolv | PolyBench | development | 188 | 14 | 34 |
| atax | PolyBench | development | 196 | 14 | 43 |
| lu | PolyBench | validation | 202 | 16 | 102 |
| doitgen | PolyBench | validation | 206 | 16 | 93 |
| syrk | PolyBench | validation | 213 | 16 | 78 |
| trmm | PolyBench | development | 213 | 14 | 43 |
| durbin | PolyBench | development | 215 | 15 | 65 |
| gesummv | PolyBench | development | 216 | 14 | 34 |
| nussinov | PolyBench | development | 216 | 16 | 121 |
| heat-3d | PolyBench | development | 216 | 14 | 58 |
| covariance | PolyBench | development | 222 | 14 | 52 |
| bicg | PolyBench | test | 224 | 14 | 43 |
| cholesky | PolyBench | test | 226 | 16 | 96 |
| mvt | PolyBench | development | 229 | 16 | 71 |
| syr2k | PolyBench | validation | 232 | 16 | 78 |
| symm | PolyBench | development | 240 | 14 | 49 |
| fdtd-2d | PolyBench | validation | 247 | 15 | 89 |
| gramschmidt | PolyBench | development | 253 | 13 | 66 |
| adi | PolyBench | development | 275 | 14 | 52 |
| correlation | PolyBench | development | 278 | 14 | 61 |
| gemver | PolyBench | development | 282 | 13 | 51 |
| ludcmp | PolyBench | test | 282 | 15 | 125 |
| deriche | PolyBench | development | 340 | 16 | 120 |
| sqlite-modern | Modern applications | development | 311,070 | 3,911 | 87,927 |
| zstd | Modern applications | test | 43,441 | 1,965 | 26,818 |
| ffmpeg | Modern applications | test | 136,356 | 4,990 | 78,431 |
| pathfinder | Rodinia / MediaBench (LLVM subset) | development | 120 | 6 | 61 |
| hotspot | Rodinia / MediaBench (LLVM subset) | development | 243 | 5 | 59 |
| backprop | Rodinia / MediaBench (LLVM subset) | development | 268 | 5 | 101 |
| rawcaudio | Rodinia / MediaBench (LLVM subset) | development | 283 | 3 | 23 |
| rawdaudio | Rodinia / MediaBench (LLVM subset) | development | 283 | 3 | 23 |
| srad | Rodinia / MediaBench (LLVM subset) | test | 387 | 5 | 96 |
| encode | Rodinia / MediaBench (LLVM subset) | development | 1,479 | 26 | 255 |
| toast | Rodinia / MediaBench (LLVM subset) | test | 5,476 | 92 | 917 |
| mpeg2decode | Rodinia / MediaBench (LLVM subset) | development | 8,692 | 116 | 1,646 |
| cjpeg | Rodinia / MediaBench (LLVM subset) | development | 23,727 | 392 | 5,638 |

index.json 中的路径指向 node3 的工作目录。模拟抽样和重复运行不会增加独立程序数量；源码行数包含生成代码，不等于实际执行代码量。
