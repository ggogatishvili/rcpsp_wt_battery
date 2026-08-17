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
#include <iostream>
#include <thread>
#include <boost/program_options/parsers.hpp>
#include <boost/program_options/positional_options.hpp>
#include <boost/program_options/options_description.hpp>
#include <boost/program_options.hpp>
#include <fmt/base.h>
#include <fmt/format.h>
#include <gurobi_c++.h>
#include <nlohmann/json.hpp>
#include "helpers.h"
#include "config.h"

namespace {
   // Loads machine-profile fields present in the JSON file into Config's
   // statics; fields absent from the file keep whatever value they already
   // have (the A2 defaults, unless a preceding CLI flag already set them).
   void loadMachineProfileFile(const std::string& path)
   {
      std::ifstream file{path};
      if ( !file.is_open() )
         throw std::runtime_error(fmt::format("Could not open machine profile file {}", path));

      nlohmann::json j;
      file >> j;

      Config::eProc = j.value("e_proc", Config::eProc);
      Config::eIdle = j.value("e_idle", Config::eIdle);
      Config::eOff  = j.value("e_off",  Config::eOff);

      auto readTransition = [&](const char* key, int& time, double& cost) {
         if ( !j.contains(key) ) return;
         const auto& t = j.at(key);
         time = t.value("time", time);
         cost = t.value("cost", cost);
      };
      readTransition("off_proc",  Config::offProcTime,  Config::offProcCost);
      readTransition("proc_off",  Config::procOffTime,  Config::procOffCost);
      readTransition("proc_idle", Config::procIdleTime, Config::procIdleCost);
      readTransition("idle_proc", Config::idleProcTime, Config::idleProcCost);
   }
}


GRBEnv& Config::gurobiEnv()
{
   static std::unique_ptr<GRBEnv> env = []() {
      auto e = std::make_unique<GRBEnv>(true);
      e->set(GRB_IntParam_OutputFlag, 0);
      e->start();
      return e;
   }();
   return *env;
}


void Config::fromArgs(const int argc, const char* const argv[])
{
   namespace po = boost::program_options;

   po::options_description desc("Options");
   desc.add_options()
      ("help",    "Print help message")
      ("version", "Print version")
      ("input,i",  po::value<std::string>(), "Input file (Mandatory)")
      ("output,o", po::value<std::string>(), "Output file (default: None)")
      ("tl",  po::value<long>(),     fmt::format("Time limit in seconds (default: {})", timeLimit).c_str())
      ("thl", po::value<unsigned>(), fmt::format("Thread limit (default: {})", threadLimit).c_str())
      ("ml",  po::value<long>(),     fmt::format("Memory limit in Gb (default: {})", memoryLimit).c_str())
      ("alpha", po::value<double>(), fmt::format("⍺ value [0-1](default: {})", alpha).c_str())
      ("method,m", po::value<std::string>(),
         fmt::format("Resolution method [MILP|H1|H1P|GA|GAP|matheur|LBBD|Benders|StateLBBD] "
                     "(default: {})", method).c_str())
      ("batteryCapacity,b", po::value<int>(),
         fmt::format("Battery capacity (default: {})", batteryCapacity).c_str())
      ("verbose,v",   fmt::format("Verbose mode (default: {})", verbose).c_str())
      ("withStats,w", fmt::format("When verbose mode is false, print stats (default: {})", withStats).c_str())

      // Machine profile (C2)
      ("machine-profile", po::value<std::string>(),
         "JSON file with machine energy/transition profile (e_proc, e_idle, e_off, "
         "off_proc/proc_off/proc_idle/idle_proc {time,cost}); individual flags below override it")
      ("e-proc", po::value<double>(), fmt::format("Energy cost per unit time in Proc state (default: {})", eProc).c_str())
      ("e-idle", po::value<double>(), fmt::format("Energy cost per unit time in Idle state (default: {})", eIdle).c_str())
      ("e-off",  po::value<double>(), fmt::format("Energy cost per unit time in Off state (default: {})", eOff).c_str())
      ("off-proc-time",  po::value<int>(),    fmt::format("Off->Proc transition duration (default: {})", offProcTime).c_str())
      ("off-proc-cost",  po::value<double>(), fmt::format("Off->Proc transition energy cost per unit time (default: {})", offProcCost).c_str())
      ("proc-off-time",  po::value<int>(),    fmt::format("Proc->Off transition duration (default: {})", procOffTime).c_str())
      ("proc-off-cost",  po::value<double>(), fmt::format("Proc->Off transition energy cost per unit time (default: {})", procOffCost).c_str())
      ("proc-idle-time", po::value<int>(),    fmt::format("Proc->Idle transition duration (default: {})", procIdleTime).c_str())
      ("proc-idle-cost", po::value<double>(), fmt::format("Proc->Idle transition energy cost per unit time (default: {})", procIdleCost).c_str())
      ("idle-proc-time", po::value<int>(),    fmt::format("Idle->Proc transition duration (default: {})", idleProcTime).c_str())
      ("idle-proc-cost", po::value<double>(), fmt::format("Idle->Proc transition energy cost per unit time (default: {})", idleProcCost).c_str())

      // Battery profile (C3 / C4)
      ("charging-efficiency",    po::value<double>(), fmt::format("Battery charging efficiency [0-1] (default: {})", chargingEfficiency).c_str())
      ("discharging-efficiency", po::value<double>(), fmt::format("Battery discharging efficiency [0-1] (default: {})", dischargingEfficiency).c_str())
      ("c-rate", po::value<double>(),
         "Battery C-rate: max charge/discharge power as a multiple of capacity per hour (default: uncapped)")

      // Tardiness cost scale (C5)
      ("lambda", po::value<double>(),
         fmt::format("Tardiness cost scale, multiplies every task weight on load (default: {})", lambda).c_str())

      // GA / MatH shared params
      ("seed,s",    po::value<uint32_t>(), "RNG Seed (default: Random)")
      ("popSize",   po::value<int>(), fmt::format("Population Size (default: {})", populationSize).c_str())
      ("stagLimit", po::value<int>(), fmt::format("Stagnation Limit (default: {})", stagnationLimit).c_str())

      // Crossover Weights
      ("wcSkip",  po::value<int>(), "Crossover Weight: Skip")
      ("wcPrio",  po::value<int>(), "Crossover Weight: Priority Only")
      ("wcDelay", po::value<int>(), "Crossover Weight: Delay Only")
      ("wcBoth",  po::value<int>(), "Crossover Weight: Both")

      // Mutator Strategy Weights
      ("wmSkip",  po::value<int>(), "Mutator Weight: Skip")
      ("wmPrio",  po::value<int>(), "Mutator Weight: Priority Only")
      ("wmDelay", po::value<int>(), "Mutator Weight: Delay Only")
      ("wmBoth",  po::value<int>(), "Mutator Weight: Both")

      // Mutator Priority Weights & Shift
      ("wmPrioKeep",  po::value<int>(),    "Mutator Prio Weight: Keep")
      ("wmPrioNew",   po::value<int>(),    "Mutator Prio Weight: New")
      ("wmPrioShift", po::value<int>(),    "Mutator Prio Weight: Shift")
      ("mPrioMag",    po::value<double>(), "Mutator Prio Shift Magnitude")

      // Mutator Delay Weights & Shift
      ("wmDelayKeep",   po::value<int>(),    "Mutator Delay Weight: Keep")
      ("wmDelayZero",   po::value<int>(),    "Mutator Delay Weight: Zero")
      ("wmDelayNewRnd", po::value<int>(),    "Mutator Delay Weight: New Random")
      ("wmDelayNewChp", po::value<int>(),    "Mutator Delay Weight: New Cheap")
      ("wmDelayShift",  po::value<int>(),    "Mutator Delay Weight: Shift")
      ("mDelayMag",     po::value<double>(), "Mutator Delay Shift Magnitude")

      // H1P / GAP params
      ("states", po::value<std::string>(),
         fmt::format("Machine states usable between Proc blocks "
                     "[all|proc,idle|proc] (default: {}). Restricts the SPACES "
                     "graph only; the mandatory Off at t=0 and t=h-1 is "
                     "unaffected.", to_string(stateSet)).c_str())
      ("phase1-price-aware", "Enable price-aware EI delay in Phase 1 (H1P / GAP)")
      ("phase1-window", po::value<int>(),
         fmt::format("Max delay window for price-aware Phase 1 (default: {})", phase1Window).c_str())
      ("phase3-lp", fmt::format("Replace greedy Phase 3 with exact battery LP (H1P / GAP) (default: {})", phase3LP).c_str())

      // LBBD params
      ("sub-tl", po::value<double>(),
         fmt::format("LBBD: per-call time limit of the RCPSP subproblem, in seconds (default: {})",
                     subproblemTimeLimit).c_str())
      ("refine-tl", po::value<double>(),
         fmt::format("LBBD: time budget for isolating a minimal infeasible subset (default: {})",
                     conflictRefinerTimeLimit).c_str())
      ("no-warmstart", "LBBD/Benders: do not seed the master with the H1 schedule")
      ("lbbd-tardiness-bounds", po::value<int>(),
         fmt::format("LBBD: EI-ancestor tardiness bounds written per task in the master (default: {})",
                     lbbdTardinessBoundsPerTask).c_str())
      ("lbbd-partition",
         "LBBD: model the machine timeline with the interval-partition constraint "
         "instead of the unit flow (same solutions, larger model)")
      ("no-benders-node-cuts",
         "Benders: separate battery cuts only at incumbents, not at fractional nodes")
      ("battery-free-end",
         "Let the battery end the horizon non-empty (pre-fix behaviour of BatteryLp; "
         "note SolverMILP always forces it empty, so the two then disagree under negative prices)")

      // MatH params
      ("mathEliteRatio", po::value<double>(),
         fmt::format("MatH: fraction re-evaluated with MILP per generation (default: {})",
                     mathEliteRatio).c_str())
      ("mathMilpTl", po::value<double>(),
         fmt::format("MatH: per-evaluation MILP time limit in seconds (default: {})",
                     mathMilpTimeLimit).c_str())
   ;

   po::positional_options_description pod;
   pod.add("input", -1);

   po::variables_map vm;
   try {
      po::store(po::command_line_parser(argc, argv).options(desc).positional(pod).run(), vm);
      po::notify(vm);
   } catch (const po::error& e) {
      fmt::println("{}", e.what());
      exit_final(1);
   }

   if (vm.contains("help")) {
      desc.print(std::cout);
      exit_final();
   }

   if (vm.contains("version")) {
      fmt::println("rcpsp solver version {}", VERSION);
      exit_final();
   }

   if (vm.contains("output"))          Config::outputFile      = vm["output"].as<std::string>();
   if (vm.contains("input"))           Config::inputFile       = vm["input"].as<std::string>();
   if (vm.contains("tl"))              Config::timeLimit       = vm["tl"].as<long>();
   if (vm.contains("thl"))             Config::threadLimit     = vm["thl"].as<unsigned>();
   if (vm.contains("ml"))              Config::memoryLimit     = vm["ml"].as<long>();
   if (vm.contains("alpha"))           Config::alpha           = vm["alpha"].as<double>();
   if (vm.contains("method"))          Config::method          = parseResolutionMethod(vm["method"].as<std::string>());
   if (vm.contains("batteryCapacity")) Config::batteryCapacity = vm["batteryCapacity"].as<int>();
   if (vm.contains("verbose"))         Config::verbose         = true;
   if (vm.contains("withStats"))       Config::withStats       = true;

   // Machine profile (C2) — load the file first, then apply individual
   // overrides so a specific flag always wins over the file.
   if (vm.contains("machine-profile")) {
      Config::machineProfileFile = vm["machine-profile"].as<std::string>();
      loadMachineProfileFile(Config::machineProfileFile.value());
   }
   if (vm.contains("e-proc")) Config::eProc = vm["e-proc"].as<double>();
   if (vm.contains("e-idle")) Config::eIdle = vm["e-idle"].as<double>();
   if (vm.contains("e-off"))  Config::eOff  = vm["e-off"].as<double>();
   if (vm.contains("off-proc-time"))  Config::offProcTime  = vm["off-proc-time"].as<int>();
   if (vm.contains("off-proc-cost"))  Config::offProcCost  = vm["off-proc-cost"].as<double>();
   if (vm.contains("proc-off-time"))  Config::procOffTime  = vm["proc-off-time"].as<int>();
   if (vm.contains("proc-off-cost"))  Config::procOffCost  = vm["proc-off-cost"].as<double>();
   if (vm.contains("proc-idle-time")) Config::procIdleTime = vm["proc-idle-time"].as<int>();
   if (vm.contains("proc-idle-cost")) Config::procIdleCost = vm["proc-idle-cost"].as<double>();
   if (vm.contains("idle-proc-time")) Config::idleProcTime = vm["idle-proc-time"].as<int>();
   if (vm.contains("idle-proc-cost")) Config::idleProcCost = vm["idle-proc-cost"].as<double>();

   // Battery profile (C3 / C4)
   if (vm.contains("charging-efficiency"))    Config::chargingEfficiency    = vm["charging-efficiency"].as<double>();
   if (vm.contains("discharging-efficiency")) Config::dischargingEfficiency = vm["discharging-efficiency"].as<double>();
   if (vm.contains("c-rate"))                 Config::cRate                 = vm["c-rate"].as<double>();

   // Tardiness cost scale (C5)
   if (vm.contains("lambda")) Config::lambda = vm["lambda"].as<double>();

   // GA / MatH shared params
   if (vm.contains("seed"))      Config::seed            = vm["seed"].as<uint32_t>();
   if (vm.contains("popSize"))   Config::populationSize  = vm["popSize"].as<int>();
   if (vm.contains("stagLimit")) Config::stagnationLimit = vm["stagLimit"].as<int>();

   if (vm.contains("wcSkip"))  Config::weightCrossSkip         = vm["wcSkip"].as<int>();
   if (vm.contains("wcPrio"))  Config::weightCrossPriorityOnly = vm["wcPrio"].as<int>();
   if (vm.contains("wcDelay")) Config::weightCrossDelayOnly    = vm["wcDelay"].as<int>();
   if (vm.contains("wcBoth"))  Config::weightCrossBoth         = vm["wcBoth"].as<int>();

   if (vm.contains("wmSkip"))  Config::weightMutSkip         = vm["wmSkip"].as<int>();
   if (vm.contains("wmPrio"))  Config::weightMutPriorityOnly = vm["wmPrio"].as<int>();
   if (vm.contains("wmDelay")) Config::weightMutDelayOnly    = vm["wmDelay"].as<int>();
   if (vm.contains("wmBoth"))  Config::weightMutBoth         = vm["wmBoth"].as<int>();

   if (vm.contains("wmPrioKeep"))  Config::weightMutPrioKeep  = vm["wmPrioKeep"].as<int>();
   if (vm.contains("wmPrioNew"))   Config::weightMutPrioNew   = vm["wmPrioNew"].as<int>();
   if (vm.contains("wmPrioShift")) Config::weightMutPrioShift = vm["wmPrioShift"].as<int>();
   if (vm.contains("mPrioMag"))    Config::mutPrioShiftMag    = vm["mPrioMag"].as<double>();

   if (vm.contains("wmDelayKeep"))   Config::weightMutDelayKeep      = vm["wmDelayKeep"].as<int>();
   if (vm.contains("wmDelayZero"))   Config::weightMutDelayZero      = vm["wmDelayZero"].as<int>();
   if (vm.contains("wmDelayNewRnd")) Config::weightMutDelayNewRandom = vm["wmDelayNewRnd"].as<int>();
   if (vm.contains("wmDelayNewChp")) Config::weightMutDelayNewCheap  = vm["wmDelayNewChp"].as<int>();
   if (vm.contains("wmDelayShift"))  Config::weightMutDelayShift     = vm["wmDelayShift"].as<int>();
   if (vm.contains("mDelayMag"))     Config::mutDelayShiftMag        = vm["mDelayMag"].as<double>();

   // H1P / GAP params
   if (vm.contains("states"))             Config::stateSet         = parseStateSet(vm["states"].as<std::string>());

   // --states is honoured by the SPACES graph (H1/H1P/GA/GAP) only. The MILP
   // and the MatH decoder build their own state models and would silently
   // ignore it, returning full-model results under a sigma1/sigma2 label --
   // exactly the kind of silent wrong answer that is impossible to spot in an
   // aggregate. Refuse rather than mislead.
   if ( Config::stateSet != Config::StateSet::All
        && (Config::method == ResolutionMethod::MILP
            || Config::method == ResolutionMethod::MatH) ) {
      fmt::println(stderr,
         "--states={} is not implemented for method {}: only the SPACES-graph "
         "methods (H1, H1P, GA, GAP) honour it. Refusing rather than silently "
         "returning full-model results.",
         to_string(Config::stateSet), to_string(Config::method));
      exit_final(1);
   }
   if (vm.contains("phase1-price-aware")) Config::phase1PriceAware = true;
   if (vm.contains("phase1-window"))      Config::phase1Window     = vm["phase1-window"].as<int>();
   if (vm.contains("phase3-lp"))          Config::phase3LP         = true;

   // LBBD params
   if (vm.contains("sub-tl"))    Config::subproblemTimeLimit       = vm["sub-tl"].as<double>();
   if (vm.contains("refine-tl")) Config::conflictRefinerTimeLimit   = vm["refine-tl"].as<double>();
   if (vm.contains("no-warmstart")) Config::lbbdWarmStart          = false;
   if (vm.contains("lbbd-tardiness-bounds"))
      Config::lbbdTardinessBoundsPerTask = vm["lbbd-tardiness-bounds"].as<int>();
   if (vm.contains("lbbd-partition")) Config::lbbdIntervalPartition = true;
   if (vm.contains("no-benders-node-cuts")) Config::bendersNodeCuts     = false;
   if (vm.contains("battery-free-end"))     Config::batteryTerminalEmpty = false;

   // Same reasoning as for the MILP above: the SPACES graph inside the LBBD
   // switching matrix *does* honour --states, but the RCPSP subproblem has no
   // notion of machine states at all, so a restricted ladder is applied
   // consistently here. Nothing to refuse -- this note exists so the next
   // reader does not add LBBD to the refusal list by analogy.

   // MatH params
   if (vm.contains("mathEliteRatio")) Config::mathEliteRatio    = vm["mathEliteRatio"].as<double>();
   if (vm.contains("mathMilpTl"))     Config::mathMilpTimeLimit = vm["mathMilpTl"].as<double>();
}

void Config::showConfig()
{
   fmt::println("\nCurrent configuration:");
   fmt::println("   {:<20}{:<15}", "Method:",          method);
   fmt::println("   {:<20}{:<15}", "Input file:",       inputFile);
   fmt::println("   {:<20}{:<15}", "Output file:",      outputFile ? outputFile.value() : "None");
   fmt::println("   {:<20}{:<15}", "Time limit:",       timeLimit);
   fmt::println("   {:<20}{:<15}", "Thread limit:",     threadLimit);
   fmt::println("   {:<20}{:<15}", "Memory limit(Go):", memoryLimit);
   fmt::println("   {:<20}{:<15}", "⍺ value:",          alpha);
   fmt::println("   {:<20}{:<15}", "Verbose mode:",     verbose ? "Yes" : "No");
   fmt::println("   {:<20}{:<15}", "Battery capacity:", batteryCapacity);
   fmt::println("   {:<20}{:<15}", "Charge/discharge eff:", fmt::format("{:.3f}/{:.3f}", chargingEfficiency, dischargingEfficiency));
   fmt::println("   {:<20}{:<15}", "C-rate:", std::isfinite(cRate) ? fmt::format("{:.3f}", cRate) : std::string("uncapped"));
   fmt::println("   {:<20}{:<15}", "Lambda:", lambda);
   fmt::println("   {:<20}{:<15}", "Machine profile:", machineProfileFile ? *machineProfileFile : std::string("default (A2)"));
   fmt::println("   {:<20}{:<15}", "Machine states:",   to_string(stateSet));
   fmt::println("   {:<20}{:<15}", "Version:",          VERSION);
   if (method == ResolutionMethod::H1P || method == ResolutionMethod::GAP
       || phase1PriceAware || phase3LP) {
      fmt::println("   {:<20}{:<15}", "Phase1 price-aware:", phase1PriceAware ? "Yes" : "No");
      fmt::println("   {:<20}{:<15}", "Phase1 window:",      phase1Window);
      fmt::println("   {:<20}{:<15}", "Phase3 LP:",          phase3LP ? "Yes" : "No");
   }
   if (method == ResolutionMethod::MatH) {
      fmt::println("   {:<20}{:<15}", "MatH elite ratio:", mathEliteRatio);
      fmt::println("   {:<20}{:<15}", "MatH MILP TL:",     mathMilpTimeLimit);
   }
   if (method == ResolutionMethod::LBBD
       || method == ResolutionMethod::Benders || method == ResolutionMethod::StateLBBD) {
      fmt::println("   {:<20}{:<15}", "Subproblem TL:",    subproblemTimeLimit);
      fmt::println("   {:<20}{:<15}", "Refiner TL:",       conflictRefinerTimeLimit);
      fmt::println("   {:<20}{:<15}", "Warm start:",       lbbdWarmStart ? "Yes" : "No");
      fmt::println("   {:<20}{:<15}", "Tardiness bounds:", lbbdTardinessBoundsPerTask);
   }
   if (method == ResolutionMethod::LBBD)
      fmt::println("   {:<20}{:<15}", "Timeline model:",
                   lbbdIntervalPartition ? "interval partition" : "unit flow");
   if (method == ResolutionMethod::Benders)
      fmt::println("   {:<20}{:<15}", "Benders node cuts:", bendersNodeCuts ? "Yes" : "No");
   fmt::println("   {:<20}{:<15}", "Battery ends empty:", batteryTerminalEmpty ? "Yes" : "No");
}

void Config::init_config()
{
   Config::threadLimit = std::max(1u, std::thread::hardware_concurrency());
}

Config::StateSet Config::parseStateSet(const std::string& spec)
{
   std::string m;
   std::ranges::transform(spec, std::back_inserter(m), ::tolower);
   std::erase(m, ' ');

   if ( m == "all" || m == "off,idle,proc" || m == "proc,idle,off" )
      return Config::StateSet::All;
   if ( m == "proc,idle" || m == "idle,proc" )
      return Config::StateSet::ProcIdle;
   if ( m == "proc" )
      return Config::StateSet::ProcOnly;

   // An unrecognised value must not silently fall back to the full model: that
   // would make an E1 sigma cell secretly identical to its baseline and the
   // measured interaction meaningless.
   fmt::println(stderr, "Invalid --states value '{}'. Expected one of: "
                        "all | proc,idle | proc", spec);
   exit_final(1);
   return Config::StateSet::All;   // unreachable
}

Config::ResolutionMethod Config::parseResolutionMethod(const std::string& method)
{
   std::string m;
   std::ranges::transform(method, std::back_inserter(m), ::tolower);
   // Checked before "milp": these masters *are* ILPs and users write things
   // like "lbbd-milp", which must not silently resolve to the monolithic model.
   // "statelbbd" is checked before "lbbd" because it contains it.
   if (m.find("statelbbd") != m.npos || m.find("state-lbbd") != m.npos)
      return Config::ResolutionMethod::StateLBBD;
   if (m.find("benders") != m.npos) return Config::ResolutionMethod::Benders;
   if (m.find("lbbd")    != m.npos) return Config::ResolutionMethod::LBBD;
   if (m.find("milp")    != m.npos) return Config::ResolutionMethod::MILP;
   if (m.find("h1p")     != m.npos) return Config::ResolutionMethod::H1P;
   if (m.find("h1")      != m.npos) return Config::ResolutionMethod::H1;
   if (m.find("gap")     != m.npos) return Config::ResolutionMethod::GAP;
   if (m.find("ga")      != m.npos) return Config::ResolutionMethod::GA;
   if (m.find("matheur") != m.npos || m.find("math") != m.npos)
      return Config::ResolutionMethod::MatH;
   return Config::ResolutionMethod::None;
}