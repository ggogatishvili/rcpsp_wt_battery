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

#include <cmath>
#include <fstream>
#include <vector>
#include "jsonHandler.h"
#include "config.h"

nlohmann::json JsonHandler::toJson(const Solution& solution, const double computationTime)
{
    nlohmann::json json;

    // Basic solution informations
    nlohmann::json solutionInfo;
    solutionInfo["objective_value"] = solution.getObjVal();
    solutionInfo["energy_cost"] = solution.getEnergyCost();
    solutionInfo["tardiness_cost"] = solution.getTardinessCost();
    solutionInfo["computation_time"] = computationTime;
    solutionInfo["gap"] = solution.getStats().gap_to_optimal;
    json["solution_info"] = solutionInfo;

    // Per-method measurements, present only for methods that produce them.
    if ( !solution.getDiagnostics().empty() ) {
        nlohmann::json diagnostics;
        for (const auto& [key, value] : solution.getDiagnostics())
            diagnostics[key] = value;
        json["diagnostics"] = diagnostics;
    }

    // Task assignments
    auto& taskAssignments = solution.getTaskAssignments();
    json["task_assignments"] = nlohmann::json::array();

    // Battery levels
    json["battery_levels"] = solution.getBatteryLevels();

    const Instance* instance = solution.getInstance();

    // Create task details with timing information
    for (size_t i = 0; i < taskAssignments.size(); i++) {
        nlohmann::json taskJson;
        taskJson["task_id"] = i;
        taskJson["start_time"] = taskAssignments[i];

        // Add task details from the instance
        if ( instance && i < instance->tasks.size() ) {
            const Task& task = instance->tasks[i];
            taskJson["duration"] = task.duration();
            taskJson["end_time"] = taskAssignments[i] + task.duration() - 1; // Inclusive end time
            taskJson["successors"] = task.get_successors();
            taskJson["release_date"] = task.get_release_date();
            taskJson["due_date"] = task.get_due_date();
            taskJson["weight"] = task.get_weight();

            // Add resource requests
            taskJson["resource_requests"] = nlohmann::json(task.get_resource_requests());
        }

        json["task_assignments"].push_back(taskJson);
    }

    // Machine blocks
    json["machine_blocks"] = nlohmann::json::array();
    for (const auto& machineBlock : solution.getMachineBlocks()) {
        nlohmann::json machineBlockJson;
        machineBlockJson["start_time"] = machineBlock.startTime;
        machineBlockJson["end_time"] = machineBlock.endTime;  // Inclusive end time
        machineBlockJson["description"] = machineBlock.getDescription();
        json["machine_blocks"].push_back(machineBlockJson);
    }

    // Instance summary
    if ( instance ) {
        nlohmann::json instanceJson;
        instanceJson["resource_count"] = instance->resource_capacities.size();
        instanceJson["task_count"] = instance->tasks.size();
        instanceJson["resource_capacities"] = instance->resource_capacities;
        instanceJson["energy_costs"] = instance->costs;
        json["instance_summary"] = instanceJson;
    }

    // Configuration
    nlohmann::json configJson;
    configJson["method"] = Config::to_string(Config::method);
    configJson["time_limit"] = Config::timeLimit;
    configJson["alpha"] = Config::alpha;
    configJson["battery_capacity"] = Config::batteryCapacity;
    configJson["charging_efficiency"] = Config::chargingEfficiency;
    configJson["discharging_efficiency"] = Config::dischargingEfficiency;
    configJson["c_rate"] = std::isfinite(Config::cRate) ? nlohmann::json(Config::cRate) : nlohmann::json(nullptr);
    configJson["lambda"] = Config::lambda;
    configJson["machine_profile"] = Config::machineProfileFile.value_or("default");
    configJson["e_proc"] = Config::eProc;
    configJson["e_idle"] = Config::eIdle;
    configJson["e_off"]  = Config::eOff;
    json["config"] = configJson;

    return json;
}

void JsonHandler::saveJson(const char* fileName, const Solution& solution, const double computationTime)
{
    try {
        nlohmann::json json = toJson(solution, computationTime);

        std::ofstream file(fileName);
        if ( !file.is_open() )
            throw std::runtime_error(fmt::format("Error: Could not open file {} for writing.", fileName));

        file << json.dump(4);  // Pretty print with 4-space indentation
        file.close();
    } catch ( const std::exception& e ) {
        throw std::runtime_error(fmt::format("Error saving solution to JSON: {}.", e.what()));
    }
}

void JsonHandler::saveJson(std::string& fileName, const Solution& solution, const double computationTime)
{
    // Check if filename is valid
    if ( fileName.empty() )
        throw std::runtime_error("Runtime Error: No filename provided!");

    // If filename does not end wth .json, remove the last extension and add .json instead
    if ( !fileName.ends_with(".json") ) {
        fileName.erase(fileName.find_last_of("."));
        fileName += ".json";
    }

    // Save solution to file
    saveJson(fileName.c_str(), solution, computationTime);
}