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

#include <iostream>
#include <thread>
#include <boost/program_options/parsers.hpp>
#include <boost/program_options/positional_options.hpp>
#include <boost/program_options/options_description.hpp>
#include <boost/program_options.hpp>
#include <fmt/base.h>
#include <fmt/format.h>
#include "helpers.h"
#include "config.h"


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
         fmt::format("Resolution method [MILP|H1|GA|matheur] (default: {})", method).c_str())
      ("batteryCapacity,b", po::value<int>(),
         fmt::format("Battery capacity (default: {})", batteryCapacity).c_str())
      ("verbose,v",   fmt::format("Verbose mode (default: {})", verbose).c_str())
      ("withStats,w", fmt::format("When verbose mode is false, print stats (default: {})", withStats).c_str())

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
   fmt::println("   {:<20}{:<15}", "Version:",          VERSION);
   if (method == ResolutionMethod::MatH) {
      fmt::println("   {:<20}{:<15}", "MatH elite ratio:", mathEliteRatio);
      fmt::println("   {:<20}{:<15}", "MatH MILP TL:",     mathMilpTimeLimit);
   }
}

void Config::init_config()
{
   Config::threadLimit = std::max(1u, std::thread::hardware_concurrency());
}

Config::ResolutionMethod Config::parseResolutionMethod(const std::string& method)
{
   std::string m;
   std::ranges::transform(method, std::back_inserter(m), ::tolower);
   if (m.find("milp")    != m.npos) return Config::ResolutionMethod::MILP;
   if (m.find("h1")      != m.npos) return Config::ResolutionMethod::H1;
   if (m.find("ga")      != m.npos) return Config::ResolutionMethod::GA;
   if (m.find("matheur") != m.npos || m.find("math") != m.npos)
      return Config::ResolutionMethod::MatH;
   return Config::ResolutionMethod::None;
}