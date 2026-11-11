#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#ifndef CASE_ID
#error CASE_ID must be specified
#endif

static uint32_t next_value(uint32_t *state) {
    /* 固定种子产生可重复的数据，让两次插桩执行走过相同的分支。 */
    *state ^= *state << 13;
    *state ^= *state >> 17;
    *state ^= *state << 5;
    return *state;
}

static uint64_t mix_value(uint32_t value) {
    return ((uint64_t)value * 2654435761ULL) ^ (value >> 7);
}

__attribute__((noinline)) static uint64_t callee(uint32_t value) {
    /* 保留一个真实调用，与允许内联的 mix_value 作比较。 */
    return mix_value(value);
}

__attribute__((noinline)) static uint64_t caller_hot(uint32_t value) {
    return callee(value) + 1;
}

__attribute__((noinline)) static uint64_t caller_cold(uint32_t value) {
    return callee(value) + 3;
}

__attribute__((noinline)) static uint64_t workload(uint32_t iterations,
                                                uint32_t threshold,
                                                uint32_t seed) {
    uint64_t result = 0;
    uint32_t state = seed;
    for (uint32_t index = 0; index < iterations; ++index) {
        uint32_t value = next_value(&state);
        /* CASE_ID 在编译时选择程序结构，threshold 在运行时改变工作量。 */
#if CASE_ID == 0
        if (value % 100 < threshold)
            result += mix_value(value);
        else
            result += value;
#elif CASE_ID == 1
        result += mix_value(value);
#elif CASE_ID == 2
        result += mix_value(value);
        if (value % 1000 < threshold)
            break;
#elif CASE_ID == 3
        if (value % 100 < threshold)
            result += caller_hot(value);
        else
            result += caller_cold(value);
#elif CASE_ID == 4
        if (value % 100 < threshold)
            result += mix_value(value);
        else
            result += callee(value);
#elif CASE_ID == 5
        for (uint32_t inner = 0; inner < threshold; ++inner)
            result += mix_value(value + inner);
#else
#error Unsupported CASE_ID
#endif
    }
    return result;
}

int main(int argc, char **argv) {
    if (argc != 4)
        return 2;
    uint32_t iterations = (uint32_t)strtoul(argv[1], NULL, 10);
    uint32_t threshold = (uint32_t)strtoul(argv[2], NULL, 10);
    uint32_t seed = (uint32_t)strtoul(argv[3], NULL, 10);
    if (iterations == 0 || iterations > 100000 || threshold > 100 || seed == 0)
        return 2;
    printf("%llu\n", (unsigned long long)workload(iterations, threshold, seed));
    return 0;
}
