# Locate Gurobi.
#
# Honours GUROBI_DIR and $ENV{GUROBI_HOME} first, then falls back to a glob of
# the conventional install roots. The globs exist because the previous version
# of this file listed install directories one release at a time, so every
# Gurobi upgrade silently kept linking against the old path until someone
# noticed: 13.0.2 installs to /Library/gurobi1302 while a binary built against
# 13.0.1 still hunts for /Library/gurobi1301 and dies at load time with a dyld
# error that never mentions CMake. Sorting the matches in reverse puts the
# newest install first.
#
# Note that the library SONAME carries only major and minor (libgurobi130), so
# a patch upgrade needs no code change -- only a fresh configure, because the
# absolute path is baked in at link time and the CMake cache remembers it.
# After upgrading Gurobi, reconfigure from scratch or clear the GUROBI_*
# cache variables; rebuilding alone will not help.

set(_gurobi_globs)
foreach(_root "/Library/gurobi*" "/opt/gurobi*" "$ENV{HOME}/gurobi*"
              "C:/gurobi*")
    file(GLOB _hits ${_root})
    list(APPEND _gurobi_globs ${_hits})
endforeach()
list(SORT _gurobi_globs)
list(REVERSE _gurobi_globs)          # newest release first

set(_gurobi_hints)
foreach(_dir ${_gurobi_globs})
    list(APPEND _gurobi_hints
         ${_dir}
         ${_dir}/macos_universal2
         ${_dir}/linux64
         ${_dir}/armlinux64
         ${_dir}/win64)
endforeach()

find_path(GUROBI_INCLUDE_DIRS
    NAMES gurobi_c.h
    HINTS ${GUROBI_DIR} $ENV{GUROBI_HOME} ${_gurobi_hints}
    PATH_SUFFIXES include)

find_library(GUROBI_LIBRARY
    NAMES gurobi140 gurobi130 gurobi120 gurobi110 gurobi100 gurobi
    HINTS ${GUROBI_DIR} $ENV{GUROBI_HOME} ${_gurobi_hints}
    PATH_SUFFIXES lib)

find_library(GUROBI_CXX_LIBRARY
    NAMES gurobi_c++ libgurobi_c++
    HINTS ${GUROBI_DIR} $ENV{GUROBI_HOME} ${_gurobi_hints}
    PATH_SUFFIXES lib)
set(GUROBI_CXX_DEBUG_LIBRARY ${GUROBI_CXX_LIBRARY})

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(GUROBI DEFAULT_MSG
    GUROBI_LIBRARY GUROBI_INCLUDE_DIRS)

if(GUROBI_FOUND)
    message(STATUS "Gurobi library: ${GUROBI_LIBRARY}")
    message(STATUS "Gurobi headers: ${GUROBI_INCLUDE_DIRS}")
endif()
