#pragma once

#include <gurobi_c++.h>
#include <optional>
#include "instance.h"
#include "solution.h"
#include "Map1.h"
#include "Map2.h"
#include "Map3.h"


class SolverMILP
{
   public:
      SolverMILP(const Instance* const instance);

      Solution solve()
      {
         return _solve();
      }

      inline Solution operator()()
      {
         return solve();
      }

   private:
      std::unique_ptr<GRBEnv> env; // Pointer to the Gurobi environment
      const Instance* ins; // Pointer to the instance to solve


      Solution _solve();
};