# Makefile for building the packing shared library

# Compiler
CXX = g++

# Compiler flags
CXXFLAGS = -std=c++11 -fPIC -O2 -Wall

# Linker flags
LDFLAGS = -shared

# Source file
SRCS = libpacking.cpp

# Object file
OBJS = $(SRCS:.cpp=.o)

# Target shared library
TARGET = libpacking.so

# Default target
all: $(TARGET)

# Rule to build the shared library
$(TARGET): $(OBJS)
	$(CXX) $(LDFLAGS) -o $@ $^

# Rule to compile C++ source files into object files
%.o: %.cpp
	$(CXX) $(CXXFLAGS) -c $< -o $@

# Clean target to remove generated files
clean:
	rm -f $(OBJS) $(TARGET)

# Phony targets
.PHONY: all clean


