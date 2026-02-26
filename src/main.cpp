/*

Copyright (c) 2025, Corentin JUVIGNY

Permission to use, copy, modify, and/or distribute this software
for any purpose with or without fee is hereby granted, provided
that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR
CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

*/

#include <fmt/base.h>
#include <fmt/format.h>
#include "config.h"
#include "instance.h"
#include "Solver.h"
#include "Clock.h"
#include "jsonHandler.h"


int main(int argc, char* argv[])
{
   Config::fromArgs(argc, argv);

   try {
      const Instance instance = Instance::fromFile(Config::inputFile);

      Clock clock;
      clock.start();

      const Solution sol = solver::solve(&instance, Config::method, Config::alpha);

      clock.stop();

      if ( Config::verbose )
         fmt::println("\n{}\nElapsed time: {} sec.", sol, clock.elapsed());
      else if ( Config::withStats )
         fmt::println("{:.5f} {:.2f} {:.5f} {} {}", sol.getObjVal(), clock.elapsed()
            , sol.getStats().gap_to_optimal, sol.getStats().nbr_lazy, sol.getStats().nbr_subproblems);
      else
         fmt::println("{:.5f} {:.3f} ", sol.getObjVal(), clock.elapsed());

      if ( Config::verbose )
         Config::showConfig();

      if ( Config::outputFile.has_value() )
         JsonHandler::saveJson(Config::outputFile.value(), sol, clock.elapsed());

   } catch ( const std::exception& e ) {
      fmt::println(stderr, "{}", e.what());
      return 1;
   }

   return 0;
}