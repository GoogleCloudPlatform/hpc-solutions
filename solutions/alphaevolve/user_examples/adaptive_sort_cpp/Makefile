CC=g++
CFLAGS=-std=c++11 -O2 -Wall

all: benchmark

benchmark: src/main.cpp src/benchmark.cpp
	$(CC) $(CFLAGS) src/main.cpp src/benchmark.cpp -o benchmark

clean:
	rm -f benchmark


