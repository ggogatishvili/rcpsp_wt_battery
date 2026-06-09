find_path(GUROBI_INCLUDE_DIRS
    NAMES gurobi_c.h
    HINTS ${GUROBI_DIR} $ENV{GUROBI_HOME}
    PATH_SUFFIXES include)

find_library(GUROBI_LIBRARY
    NAMES gurobi130 gurobi120 gurobi110 gurobi100 gurobi libgurobi120
    HINTS ${GUROBI_DIR} $ENV{GUROBI_HOME}
          /Library/gurobi1301/macos_universal2
          /Library/gurobi1300/macos_universal2
          /Library/gurobi1202/macos_universal2
          /Library/gurobi1201/macos_universal2
    PATH_SUFFIXES lib)

find_library(GUROBI_CXX_LIBRARY
    NAMES gurobi_c++ libgurobi_c++
    HINTS ${GUROBI_DIR} $ENV{GUROBI_HOME}
    PATH_SUFFIXES lib)
set(GUROBI_CXX_DEBUG_LIBRARY ${GUROBI_CXX_LIBRARY})

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(GUROBI DEFAULT_MSG GUROBI_LIBRARY)
