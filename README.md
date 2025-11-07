# rcpsp

## To compile:

In the solver repository

```bash
mkdir build
conan install . --output-folder=build --build=missing
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make
```