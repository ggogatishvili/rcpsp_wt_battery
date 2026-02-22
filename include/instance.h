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

#pragma once

#include <fmt/base.h>
#include <fmt/format.h>
#include <optional>
#include <vector>
#include <map>
#include <list>
#include <numeric>
#include <gurobi_c++.h>

#define BIG_M 10e8

enum class State : int {
    Off  = 0,
    Proc = 1,
    Idle = 2
};

inline std::string_view state_name(State s) noexcept {
    switch (s) {
        case State::Off:  return "Off";
        case State::Proc: return "Proc";
        case State::Idle: return "Idle";
    }
    return "Unknown";
}

class Task
{
   public:
      Task(const int duration, const int nbr_resources, const int * const resource_requests, const int number_of_successors, const int * const successors, const int release_date, const int due_date, const double weight)
         : _duration(duration)
         , resource_requests(resource_requests, resource_requests + nbr_resources)
         , successors(successors, successors + number_of_successors)
         , release_date(release_date)
         , due_date(due_date)
         , weight(weight)
         , start_time(std::nullopt)
         , end_time(std::nullopt)
      { }

      // Getters for private members
      inline auto get_resource_requests() const & -> const std::vector<int>&  { return resource_requests; }
      inline auto get_successors() const & -> const std::vector<int>& { return successors; }
      inline auto get_release_date() const -> int { return release_date; }
      inline auto get_due_date() const -> int { return due_date; }
      inline auto get_weight() const -> double { return weight; }
      inline auto get_start_time() const -> std::optional<int> { return start_time; }
      inline auto get_end_time() const ->  std::optional<int> { return end_time; }
      inline auto duration() const -> int { return _duration; }

      inline bool is_ei_task() const { return resource_requests[0] > 0; }

      // Method to convert to string for formatting
      std::string to_string() const
      {
         std::string result = "Task(duration=" + std::to_string(_duration);

         result += ", resource_requests=[";
         for (size_t i = 0; i < resource_requests.size(); ++i) {
            if ( i > 0 ) result += ", ";
            result += std::to_string(resource_requests[i]);
         }
         result += "]";

         result += ", successors=[";
         for (size_t i = 0; i < successors.size(); ++i) {
            if ( i > 0 ) result += ", ";
            result += std::to_string(successors[i]);
         }
         result += "]";

         if ( start_time ) result += ", start_time=" + std::to_string(*start_time);
         if ( end_time ) result += ", end_time=" + std::to_string(*end_time);

         result += ")";
         return result;
      }

   private:
      int _duration;
      std::vector<int> resource_requests;
      std::vector<int> successors;
      int release_date;
      int due_date;
      double weight;
      std::optional<int> start_time;
      std::optional<int> end_time;
};

class Instance
{
   public:
      Instance() = default;
      Instance(const std::string& instancename, const std::vector<int>& _resource_capacities, const std::vector<Task>& _tasks, const std::vector<double>& _costs);

      inline auto instName() const & -> std::string { return instancename; }
      inline auto instName() const && -> std::string { return std::move(instancename); }
      inline auto nbr_tasks() const -> size_t { return tasks.size(); }
      inline auto nbr_ei_tasks() const -> size_t { return ei_tasks.size(); }
      inline auto nbr_resources() const -> size_t { return resource_capacities.size(); }
      inline auto firstOn() const -> int { return offProc.time + 1; }
      inline auto lastOn() const -> int { return lastOn(maxDuration()); }
      inline auto lastOn(const int makespan) const -> int { return makespan - procOff.time - 1; }

      inline auto maxDuration() const -> unsigned int { return costs.size(); }

      auto showInstance() const -> void;

      static auto fromFile(const std::string& fileName) -> Instance;

      inline auto cumulative_cost(const int start, const int length) const -> double
      {
         return std::accumulate(costs.begin() + start, costs.begin() + start + length, 0.0);
      }

      inline auto successors(const int t) const -> const std::vector<int>&
      {
         return tasks[t].get_successors();
      }

      inline auto rt(const int t, const int k) const -> double
      {
         return tasks[t].get_resource_requests()[k];
      }

      inline auto getProcessingTime(const int t) const -> int
      {
         return tasks[t].duration();
      }

      inline auto task_is_feasible(const int j, const int i) const -> bool
      {
         if ( j >= tasks.size() )
            return i <= maxDuration();
         else
            return i + getProcessingTime(j) - 1 <= maxDuration();
      }

      inline auto minimal_distance(const int i, const int j) const -> long
      {
         return -precedence_graph[i][j];
      }

      // Return true if j is a successor of i
      inline auto is_successor(const int j, const int i) const -> bool
      {
         return i != j && minimal_distance(i, j) >= 1;
      }

      // Return true if j is a predecessor of i
      inline auto is_antecedent(const int j, const int i) const -> bool
      {
         return is_successor(i, j);
      }

      inline auto is_ei_task(const int j) const -> bool
      {
         return tasks[j].is_ei_task();
      }

      inline double cjob(const int j, const int i) const
      {
         return i + getProcessingTime(j) - 1 > maxDuration() ? BIG_M : Proc.cost * std::accumulate(costs.cbegin() + i, costs.cbegin() + i +
                                                                                                                       getProcessingTime(j), 0.0);
      }

      // Transition costs & durations
      const struct {
         int time = 1;
         double cost = 1;
      } procOff;

      const struct {
         int time = 2;
         double cost = 5;
      } offProc;

      const struct {
         int time = 1;
         double cost = 2;
      } procIdle;

      const struct {
         int time = 1;
         double cost = 2.5;
      } idleProc;

      const struct {
         int cost = 4;
      } Proc;

      const struct {
         int cost = 0;
      } Off;

      const struct {
         int cost = 2;
      } Idle;

      const struct {
          double EF_charge = 0.95;
          double EF_discharge = 0.95;
          int B_max = 16;
      } Battery;

      std::vector<int> resource_capacities;
      std::vector<Task> tasks;
      std::vector<double> costs;
      std::vector<std::vector<long>> precedence_graph;
      std::list<int> ei_tasks;
      std::string instancename;
};

// Formatter specialization for Instance
template <> struct fmt::formatter<Instance>
{
   constexpr auto parse(format_parse_context& ctx) -> decltype(ctx.begin())
   {
      return ctx.begin();
   }

   template <typename FormatContext>
   auto format(const Instance& ins, FormatContext& ctx) const -> decltype(ctx.out())
   {
      return fmt::format_to(ctx.out(), "TODO // Solution object representation");
   }
};