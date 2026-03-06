cmake_minimum_required(VERSION 3.12)
project(rcpsp_wt_battery)
set(CMAKE_CXX_STANDARD 20)
list(INSERT CMAKE_MODULE_PATH 0 ${CMAKE_CURRENT_SOURCE_DIR})
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
include(${CMAKE_BINARY_DIR}/conan_toolchain.cmake)

# Mandatory for macos
if ( NOT XCODE )
   # workaround for https://github.com/conan-io/conan/issues/13925
   set( CMAKE_OSX_SYSROOT "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk" CACHE STRING "" FORCE )
endif()

add_executable( rcpsp_wt_battery
         src/main.cpp
         src/config.cpp
         src/precedenceGraph.cpp
         src/Solver.cpp
        src/SolverMILP.cpp
        src/SolverHeuristic1.cpp
         src/jsonHandler.cpp
         src/Clock.cpp
         src/solution.cpp
         src/instance.cpp )

set(WARNING_OPTIONS             -Wall -Wextra -Werror -Wno-vla-cxx-extension)
set(GCC_WARNING_OPTIONS         -Wduplicated-cond -Wlogical-op -Wuseless-cast -Wrestrict)
#set(ARCH_OPTIONS                -march=apple-m1)
set(RELEASE_OPTIONS             -O3)
set(DEBUG_OPTIONS               -O0 -g)
set(GDB_DEBUG_OPTIONS           -ggdb)
set(LINKER_OPTIONS              -flto)

find_package(Cplex REQUIRED)
find_package(GUROBI REQUIRED)
find_package(Boost REQUIRED)
find_package(fmt REQUIRED)
find_package(TBB REQUIRED)
find_package(nlohmann_json REQUIRED)


# Debug: Print what was found
message(STATUS "GUROBI_INCLUDE_DIRS: ${GUROBI_INCLUDE_DIRS}")
message(STATUS "GUROBI_LIBRARY: ${GUROBI_LIBRARY}")
message(STATUS "GUROBI_CXX_LIBRARY: ${GUROBI_CXX_LIBRARY}")
message(STATUS "Cplex_INCLUDE_LIBRARY: ${Cplex_INCLUDE_DIRS}")
message(STATUS "Cplex_ConstraintProgramming_INCLUDE_DIRS: ${Cplex_ConstraintProgramming_INCLUDE_DIRS}")
message(STATUS "Cplex_LIBRARIES: ${Cplex_LIBRARIES}")
message(STATUS "Cplex_ConstraintProgramming_LIBRARIES: ${Cplex_ConstraintProgramming_LIBRARIES}")

# Link Conan libraries
target_link_libraries(rcpsp_wt_battery ${CONAN_LIBS})

# Include directories
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${Cplex_INCLUDE_DIRS})
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${Cplex_ConstraintProgramming_INCLUDE_DIRS})
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${GUROBI_INCLUDE_DIRS})
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${Boost_INCLUDE_DIRS})
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE /usr/local/include/paradiseo/eo)
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE /usr/local/include/paradiseo/mo)
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE /usr/local/include/paradiseo/smp)
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)

# Link modern CMake targets
target_link_libraries(rcpsp_wt_battery PRIVATE fmt::fmt)
target_link_libraries(rcpsp_wt_battery PRIVATE onetbb::onetbb)
target_link_libraries(rcpsp_wt_battery PRIVATE boost::boost)
target_link_libraries(rcpsp_wt_battery PRIVATE nlohmann_json::nlohmann_json)

# Add library search directories first
target_link_directories(rcpsp_wt_battery PRIVATE /usr/local/lib64)


# Link solver libraries
target_link_libraries(rcpsp_wt_battery PRIVATE ${Cplex_ConstraintProgramming_LIBRARIES})
target_link_libraries(rcpsp_wt_battery PRIVATE ${Cplex_LIBRARIES})
target_link_libraries(rcpsp_wt_battery PRIVATE ${GUROBI_CXX_LIBRARY})
target_link_libraries(rcpsp_wt_battery PRIVATE ${GUROBI_LIBRARY})

# Link additional libraries (ParadisEO and system libraries)
#target_link_libraries(rcpsp_wt_battery PRIVATE boost_program_options eo eoutils es ga dl)
target_link_libraries(rcpsp_wt_battery PRIVATE dl)

# Apply linker options
target_link_options(rcpsp_wt_battery PRIVATE ${LINKER_OPTIONS})

# Compile options (uncommented and organized)
#target_compile_options(rcpsp_wt_battery PRIVATE ${WARNING_OPTIONS})
target_compile_options(rcpsp_wt_battery PRIVATE $<$<CONFIG:RELEASE>:${RELEASE_OPTIONS}>)
target_compile_options(rcpsp_wt_battery PRIVATE $<$<CONFIG:DEBUG>:${DEBUG_OPTIONS}>)
target_compile_options(rcpsp_wt_battery PRIVATE $<$<AND:$<CONFIG:DEBUG>,$<CXX_COMPILER_ID:GNU>>:${GDB_DEBUG_OPTIONS}>)
