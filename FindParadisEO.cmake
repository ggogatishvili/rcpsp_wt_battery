set(PARADISEO_SEARCH_PATHS
        $ENV{HOME}/.local
        $ENV{HOME}/paradiseo
        /root/.local
        /usr/local
)

find_path(PARADISEO_BASE_INCLUDE_DIR
        NAMES eo
        HINTS ${PARADISEO_SEARCH_PATHS}
        PATH_SUFFIXES include/paradiseo include
)

if(PARADISEO_BASE_INCLUDE_DIR)
    set(PARADISEO_INCLUDE_DIRS
            ${PARADISEO_BASE_INCLUDE_DIR}
            ${PARADISEO_BASE_INCLUDE_DIR}/eo
            ${PARADISEO_BASE_INCLUDE_DIR}/eo/es
            ${PARADISEO_BASE_INCLUDE_DIR}/eo/ga
            ${PARADISEO_BASE_INCLUDE_DIR}/eo/algo
            ${PARADISEO_BASE_INCLUDE_DIR}/eo/utils
            ${PARADISEO_BASE_INCLUDE_DIR}/mo
    )
endif()

set(PARADISEO_COMPONENTS eo eoutils es ga mo smp)
set(PARADISEO_LIBRARIES "")

foreach(COMP IN LISTS PARADISEO_COMPONENTS)
    find_library(PARADISEO_LIB_${COMP}
            NAMES ${COMP}
            HINTS ${PARADISEO_SEARCH_PATHS}
            PATH_SUFFIXES lib64 lib
    )
    if(PARADISEO_LIB_${COMP})
        list(APPEND PARADISEO_LIBRARIES ${PARADISEO_LIB_${COMP}})
    endif()
endforeach()

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(ParadisEO
        DEFAULT_MSG
        PARADISEO_LIBRARIES
        PARADISEO_BASE_INCLUDE_DIR
)