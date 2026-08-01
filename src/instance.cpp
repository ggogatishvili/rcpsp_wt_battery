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

#include <algorithm>
#include <fmt/base.h>
#include <fstream>
#include <iostream>
#include <istream>
#include <memory>
#include <nlohmann/json.hpp>
#include <precedenceGraph.h>
#include <sstream>
#include <stdexcept>
#include <fmt/format.h>
#include <string>
#include <ranges>
#include "instance.h"
#include "helpers.h"

MachineProfile MachineProfile::fromJsonFile(const std::string& path)
{
   std::ifstream file{path};
   if ( !file.is_open() )
      throw std::runtime_error(fmt::format("Could not open machine profile file {}", path));

   nlohmann::json j;
   file >> j;

   MachineProfile p; // starts from the A2 defaults; only overrides what's present

   p.eProc = j.value("e_proc", p.eProc);
   p.eIdle = j.value("e_idle", p.eIdle);
   p.eOff  = j.value("e_off",  p.eOff);

   auto readTransition = [&](const char* key, auto& field) {
      if ( !j.contains(key) ) return;
      const auto& t = j.at(key);
      field.time = t.value("time", field.time);
      field.cost = t.value("cost", field.cost);
   };
   readTransition("off_proc",  p.offProc);
   readTransition("proc_off",  p.procOff);
   readTransition("proc_idle", p.procIdle);
   readTransition("idle_proc", p.idleProc);

   return p;
}

Instance::Instance(const std::string& _instance_name, const std::vector<int>& _resource_capacities, const std::vector<Task>& _tasks, const std::vector<double>& _costs, int battery_capacity,
                    const MachineProfile& profile, double chargingEfficiency, double dischargingEfficiency, double cRate)
   : instancename(_instance_name)
   , resource_capacities(_resource_capacities)
   , tasks(_tasks)
   , costs(_costs)
   , Battery{ .chargingEfficiency = chargingEfficiency, .dischargingEfficiency = dischargingEfficiency, .batteryCapacity = battery_capacity, .cRate = cRate }
{
   Proc.cost = profile.eProc;
   Off.cost  = profile.eOff;
   Idle.cost = profile.eIdle;
   offProc  = { profile.offProc.time,  profile.offProc.cost  };
   procOff  = { profile.procOff.time,  profile.procOff.cost  };
   procIdle = { profile.procIdle.time, profile.procIdle.cost };
   idleProc = { profile.idleProc.time, profile.idleProc.cost };

   if ( offProc.time < 1 || procOff.time < 1 || procIdle.time < 1 || idleProc.time < 1 )
      throw std::runtime_error("Machine profile transition durations must be >= 1 (0 is reserved to mean \"no such transition\").");

   // Computation of basic information
   std::ranges::for_each( std::views::iota(0ul, tasks.size())
                        | std::views::filter([this](const auto i) { return tasks[i].is_ei_task(); })
                        , [this](const auto i) { ei_tasks.push_back(i); } );

   // Computation of precedence graph
   // precedence_graph = PrecedenceGraph(this)();
}

void Instance::showInstance() const
{
   fmt::println("Instance Information:");
   fmt::println("Number of tasks: {}", tasks.size());
   fmt::println("Number of resources: {}", resource_capacities.size());

   fmt::println("\nResource Capacities:");
   for (size_t i = 0; i < resource_capacities.size(); i++) {
     fmt::println("Resource {}: {}", i, resource_capacities[i]);
   }

   fmt::println("\nTasks:");
   for (size_t i = 0; i < tasks.size(); i++) {
     fmt::println("Task {}: {}", i, tasks[i].to_string());
   }

   if ( !costs.empty() ) {
     fmt::println("\nCosts:");
     for (size_t i = 0; i < costs.size(); i++) {
       fmt::println("Cost {}: {}", i, costs[i]);
     }
   }
}

Instance Instance::from(const std::string& fileName, int battery_capacity,
                         const MachineProfile& profile, double chargingEfficiency,
                         double dischargingEfficiency, double cRate, double lambda)
{
   std::vector<int> resource_capacities;
   std::vector<Task> tasks;
   std::vector<double> costs;

   std::ifstream file { fileName };

   if ( !file.is_open() )
      throw std::runtime_error(fmt::format("Could not open file {}", fileName));

   int nbr_tasks;
   int nbr_resources;
   std::string line;
   std::getline(file, line);
   std::istringstream iss(line);
   iss >> nbr_tasks >> nbr_resources;

   if ( iss.fail() || nbr_resources == 0 || nbr_tasks == 0 )
      throw std::runtime_error("Invalid instance file: either nbr of tasks or nbr of resources is null");

   std::getline(file, line);
   Helpers::setSSLine(iss, line);
   resource_capacities.reserve(nbr_resources);
   for (int i = 0; i < nbr_resources; i++)
      resource_capacities.push_back(Helpers::readValue<int>(iss));

   tasks.reserve(nbr_tasks);
   for (int i = 0; i < nbr_tasks; i++) {
      std::getline(file, line);
      Helpers::setSSLine(iss, line);

      int duration = Helpers::readValue<int>(iss);

      auto resources = std::make_unique<int[]>(nbr_resources);
      for (int j = 0; j < nbr_resources && iss.good(); j++)
         resources[j] = Helpers::readValue<int>(iss);

      int nbr_successors = Helpers::readValue<int>(iss);
      auto successors = std::make_unique<int[]>(nbr_successors);
      for (int j = 0; j < nbr_successors; j++)
         successors[j] = Helpers::readValue<int>(iss);

      int release_date = Helpers::readValue<int>(iss);
      int due_date = Helpers::readValue<int>(iss);
      auto weight = Helpers::readValue<double>(iss) * lambda; // C5: tardiness cost scale

      tasks.emplace_back(duration, nbr_resources, resources.get(), nbr_successors, successors.get(), release_date, due_date, weight);
   }

   std::getline(file, line);

   file.close();

   Helpers::setSSLine(iss, line);
   while ( !iss.eof() ) {
      double v = 0;
      iss >> v;
      if ( !iss.fail() )
         costs.push_back(v);
   }

   return Instance { fileName, resource_capacities, tasks, costs, battery_capacity, profile, chargingEfficiency, dischargingEfficiency, cRate };
}