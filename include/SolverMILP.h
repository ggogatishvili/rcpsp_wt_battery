#pragma once

#include <gurobi_c++.h>
#include "instance.h"
#include "solution.h"


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
      const Instance* ins;


      Solution _solve();
};