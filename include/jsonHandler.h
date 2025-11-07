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

#include <solution.h>
#include <nlohmann/json.hpp>

/**
 * @brief JsonHandler class
 *
 * Saves the solution in a json file
 *
 * The JSON format is as follows:
 * @code{json}
 * {
 *    "objective_value": 1234.5,
 *    "computation_time": 80,
 *    "makespan": 18,
 *    "task_assignments": [
 *       {
 *          "task_id": 1,
 *          "start_time": 0,
 *          "duration": 10,
 *          "end_time": 10,
 *          "resource_requests": [1, 2, 3]
 *       },
 *       {
 *          "task_id": 2,
 *          "start_time": 5,
 *          "duration": 15,
 *          "end_time": 20,
 *          "resource_requests": [2, 4]
 *       }
 *    ],
 *    "machine_transitions": [
 *       {
 *          "from_state": "state1",
 *          "to_state": "state2"
 *       },
 *       {
 *          "from_state": "state2",
 *          "to_state": "state3"
 *       }
 *    ],
 *    "instance_summary": {
 *       "resource_count": 5,
 *       "task_count": 2
 *    },
 *    "config": {
 *       "time_limit": 10
 *    }
 * }
 * @endcode
 */
class JsonHandler
{
   public:
      /**
      * @brief Save a solution to a JSON file (advised function)
      *
      * @param fileName Path to the output JSON file (if the file does not end with .json, it will be added)
      * @param solution Solution object to save
      * @param computationTime Computation time in seconds (-1 means no computation time given)
      */
      static void saveJson(std::string& fileName, const Solution& solution, const double computationTime = -1);
      /**
      * @brief Save a solution to a JSON file
      *
      * @param fileName Path to the output JSON file
      * @param solution Solution object to save
      * @param computationTime Computation time in seconds
      */
      static void saveJson(const char* fileName, const Solution& solution, const double computationTime);

   private:
      /**
      * @brief Convert a solution to a JSON object
      *
      * @param solution Solution object to convert
      * @param computationTime Computation time in seconds
      * @return nlohmann::json JSON representation of the solution
      */
      static nlohmann::json toJson(const Solution& solution, const double computationTime);
};