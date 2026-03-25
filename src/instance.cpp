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
#include <precedenceGraph.h>
#include <sstream>
#include <fmt/format.h>
#include <string>
#include <ranges>
#include "instance.h"
#include "helpers.h"

Instance::Instance(const std::string& _instance_name, const std::vector<int>& _resource_capacities, const std::vector<Task>& _tasks, const std::vector<double>& _costs, int battery_capacity)
   : instancename(_instance_name)
   , resource_capacities(_resource_capacities)
   , tasks(_tasks)
   , costs(_costs)
   , Battery{ .batteryCapacity = battery_capacity }
{
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

Instance Instance::from(const std::string& fileName, int battery_capacity)
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
      auto weight = Helpers::readValue<double>(iss);

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

   return Instance { fileName, resource_capacities, tasks, costs, battery_capacity };
}