#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

extern int draper_original_main(int argc, char **argv);

int main(int argc, char **argv) {
    if (argc != 2)
        return 2;
    char *end;
    errno = 0;
    long repetitions = strtol(argv[1], &end, 10);
    if (errno || *end || repetitions < 1 || repetitions > 1000000)
        return 2;
    struct timespec start, finish;
    if (clock_gettime(CLOCK_MONOTONIC, &start))
        return 3;
    for (long repeat = 0; repeat < repetitions; ++repeat) {
        /* 每轮调用原版入口，保留初始化、预热和上游结果检查。 */
        if (draper_original_main(1, argv))
            return 4;
    }
    if (clock_gettime(CLOCK_MONOTONIC, &finish))
        return 3;
    double seconds = finish.tv_sec - start.tv_sec + (finish.tv_nsec - start.tv_nsec) * 1e-9;
    printf("%.9f\n", seconds);
    return 0;
}
