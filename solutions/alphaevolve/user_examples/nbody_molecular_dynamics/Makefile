CXX = mpicxx
CXXFLAGS = -O3 -march=native -std=c++17

.PHONY: all clean

all: nbody_sim

nbody_sim: main.cpp
	$(CXX) $(CXXFLAGS) main.cpp -o nbody_sim
	cp nbody_sim /var/tmp/nbody_sim || true

clean:
	rm -f nbody_sim /var/tmp/nbody_sim



