// Native entry point of "PhD Studio.app": loads the project's Python in-process and runs
// app.desktop, so macOS sees a real app (name, Dock icon, ⌘Tab, single instance)
// instead of a bare "python" process. Paths are baked in by make_app.sh at build time.
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#ifndef PYLIB
#error "build with make_app.sh"
#endif

int main(int argc, char **argv) {
    (void)argc;
    setenv("PYTHONHOME", PYHOME, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONUNBUFFERED", "1", 1);
    if (chdir(STUDIO) != 0) {
        perror("chdir " STUDIO);
        return 1;
    }
    const char *home = getenv("HOME");
    if (home) {
        char log[2048];
        snprintf(log, sizeof log, "%s/Library/Logs/PhD Studio.log", home);
        if (!freopen(log, "a", stdout) || !freopen(log, "a", stderr)) {
            perror("log");
        }
    }
    void *lib = dlopen(PYLIB, RTLD_NOW | RTLD_GLOBAL);
    if (!lib) {
        fprintf(stderr, "PhD Studio: cannot load %s: %s\n", PYLIB, dlerror());
        return 1;
    }
    int (*py_main)(int, char **) = (int (*)(int, char **))dlsym(lib, "Py_BytesMain");
    if (!py_main) {
        fprintf(stderr, "PhD Studio: Py_BytesMain missing\n");
        return 1;
    }
    char *args[] = {
        argv[0], "-c",
        "import site, sys, runpy; site.addsitedir(" "'" SITE "'" "); sys.path.insert(0, '" STUDIO "'); "
        "runpy.run_module('app.desktop', run_name='__main__')",
        NULL,
    };
    return py_main(3, args);
}
