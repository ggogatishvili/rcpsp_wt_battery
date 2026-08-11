cmake_minimum_required(VERSION 3.12)
project(rcpsp_wt_battery)
set(CMAKE_CXX_STANDARD 23)
list(INSERT CMAKE_MODULE_PATH 0 ${CMAKE_CURRENT_SOURCE_DIR})
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
include(${CMAKE_BINARY_DIR}/conan_toolchain.cmake)

# Mandatory for macos
if ( NOT XCODE )
   # workaround for https://github.com/conan-io/conan/issues/13925
   set( CMAKE_OSX_SYSROOT "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk" CACHE STRING "" FORCE )
endif()

# Logic-based Benders decomposition: the RCPSP subproblem has two
# interchangeable backends. CP Optimizer is the one the original LBBD in the
# `rcpsp` repository uses and is markedly stronger here, but it needs a CPLEX
# licence, so the Gurobi MILP backend is the default and keeps the build
# self-contained. Exactly one of the two is compiled.
option(WITH_CPOPTIMIZER "Use CPLEX CP Optimizer for the LBBD subproblem" OFF)

if ( WITH_CPOPTIMIZER )
   set(LBBD_SUBPROBLEM_SRC src/lbbd/RcpspSubproblemCp.cpp)
else()
   set(LBBD_SUBPROBLEM_SRC src/lbbd/RcpspSubproblemMilp.cpp)
endif()

add_executable( rcpsp_wt_battery
        src/main.cpp
        src/config.cpp
        src/precedenceGraph.cpp
        src/Solver.cpp
        src/SolverMILP.cpp
        src/SolverH1.cpp
        src/SolverH1P.cpp
        src/SolverGA.cpp
        src/SolverGAP.cpp
        src/BatteryLp.cpp
        src/jsonHandler.cpp
        src/Clock.cpp
        src/solution.cpp
        src/instance.cpp
        src/ResourceFlow.cpp
        src/SpacesMilpDecoder.cpp
        src/SolverMatH.cpp
        src/SolverLBBD.cpp
        src/SolverBenders.cpp
        src/lbbd/PrecedenceClosure.cpp
        src/lbbd/SwitchingGraph.cpp
        src/lbbd/TimeWindows.cpp
        src/lbbd/EiPlacement.cpp
        src/lbbd/MachineStateModel.cpp
        src/lbbd/DecompositionReport.cpp
        src/lbbd/BatteryPostprocess.cpp
        ${LBBD_SUBPROBLEM_SRC} )

set(WARNING_OPTIONS             -Wall -Wextra -Werror -Wno-vla-cxx-extension)
set(GCC_WARNING_OPTIONS         -Wduplicated-cond -Wlogical-op -Wuseless-cast -Wrestrict)
#set(ARCH_OPTIONS                -march=apple-m1)
set(RELEASE_OPTIONS             -O3)
set(DEBUG_OPTIONS               -O0 -g)
set(GDB_DEBUG_OPTIONS           -ggdb)
set(LINKER_OPTIONS              -flto)

if ( WITH_CPOPTIMIZER )
   find_package(Cplex REQUIRED)
endif()
find_package(GUROBI REQUIRED)
find_package(Boost REQUIRED)
find_package(fmt REQUIRED)
find_package(TBB REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(ParadisEO REQUIRED)
#find_package(OpenMP REQUIRED)
find_library(EO_LIB      eo      PATHS /usr/local/lib64 REQUIRED)
find_library(EOUTILS_LIB eoutils PATHS /usr/local/lib64 REQUIRED)


# Debug: Print what was found
message(STATUS "GUROBI_INCLUDE_DIRS: ${GUROBI_INCLUDE_DIRS}")
message(STATUS "GUROBI_LIBRARY: ${GUROBI_LIBRARY}")
message(STATUS "GUROBI_CXX_LIBRARY: ${GUROBI_CXX_LIBRARY}")
message(STATUS "EO_LIB: ${EO_LIB}")
message(STATUS "EOUTILS_LIB: ${EOUTILS_LIB}")
if ( WITH_CPOPTIMIZER )
   message(STATUS "Cplex_INCLUDE_LIBRARY: ${Cplex_INCLUDE_DIRS}")
   message(STATUS "Cplex_ConstraintProgramming_INCLUDE_DIRS: ${Cplex_ConstraintProgramming_INCLUDE_DIRS}")
   message(STATUS "Cplex_LIBRARIES: ${Cplex_LIBRARIES}")
   message(STATUS "Cplex_ConstraintProgramming_LIBRARIES: ${Cplex_ConstraintProgramming_LIBRARIES}")
endif()

# Link Conan libraries
target_link_libraries(rcpsp_wt_battery ${CONAN_LIBS})

# Include directories
if ( WITH_CPOPTIMIZER )
   target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${Cplex_INCLUDE_DIRS})
   target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${Cplex_ConstraintProgramming_INCLUDE_DIRS})
endif()
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${GUROBI_INCLUDE_DIRS})
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${Boost_INCLUDE_DIRS})
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${PARADISEO_INCLUDE_DIRS})
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE /usr/local/include/paradiseo/eo)
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE /usr/local/include/paradiseo/mo)
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE /usr/local/include/paradiseo/smp)
target_include_directories(rcpsp_wt_battery SYSTEM PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)

# Link modern CMake targets
target_link_libraries(rcpsp_wt_battery PRIVATE fmt::fmt)
target_link_libraries(rcpsp_wt_battery PRIVATE onetbb::onetbb)
target_link_libraries(rcpsp_wt_battery PRIVATE boost::boost)
target_link_libraries(rcpsp_wt_battery PRIVATE nlohmann_json::nlohmann_json)
#target_link_libraries(rcpsp_wt_battery PRIVATE OpenMP::OpenMP_CXX)

# Link solver libraries
if ( WITH_CPOPTIMIZER )
   target_link_libraries(rcpsp_wt_battery PRIVATE ${Cplex_ConstraintProgramming_LIBRARIES})
   target_link_libraries(rcpsp_wt_battery PRIVATE ${Cplex_LIBRARIES})
endif()
target_link_libraries(rcpsp_wt_battery PRIVATE ${GUROBI_CXX_LIBRARY})
target_link_libraries(rcpsp_wt_battery PRIVATE ${GUROBI_LIBRARY})

# Link additional libraries (ParadisEO and system libraries)
#target_link_libraries(rcpsp_wt_battery PRIVATE ${PARADISEO_LIBRARIES})
target_link_libraries(rcpsp_wt_battery PRIVATE ${EO_LIB} ${EOUTILS_LIB} dl)

# Apply linker options
target_link_options(rcpsp_wt_battery PRIVATE ${LINKER_OPTIONS})

# Compile options (uncommented and organized)
#target_compile_options(rcpsp_wt_battery PRIVATE ${WARNING_OPTIONS})
target_compile_options(rcpsp_wt_battery PRIVATE $<$<CONFIG:RELEASE>:${RELEASE_OPTIONS}>)
target_compile_options(rcpsp_wt_battery PRIVATE $<$<CONFIG:DEBUG>:${DEBUG_OPTIONS}>)
target_compile_options(rcpsp_wt_battery PRIVATE $<$<AND:$<CONFIG:DEBUG>,$<CXX_COMPILER_ID:GNU>>:${GDB_DEBUG_OPTIONS}>)
