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
      ("help", "Print help message")
      ("version", "Print version")
      ("input,i", po::value<std::string>(), "Input file (Mandatory)")
      ("output,o", po::value<std::string>(), "Output file (default: None)")
      ("tl", po::value<long>(), fmt::format("Time limit in seconds (default: {})", timeLimit).c_str())
      ("thl", po::value<unsigned>(), fmt::format("Thread limit (default: {})", threadLimit).c_str())
      ("ml", po::value<long>(), fmt::format("Memory limit in Gb (default: {})", memoryLimit).c_str())
      ("alpha", po::value<double>(), fmt::format("⍺ value [0-1](default: {})", alpha).c_str())
      ("method,m", po::value<std::string>(), fmt::format("Resolution method [ILP] (default: {})", method).c_str())
      ("verbose,v", fmt::format("Verbose mode (default: {})", verbose).c_str())
      ("withStats,w", fmt::format("When verbose mode is false, print stats (default: {})", withStats).c_str());

   po::positional_options_description pod;
   pod.add("input", -1);

   po::variables_map vm;
   try {
      po::store(po::command_line_parser(argc, argv).options(desc).positional(pod).run(), vm);
      po::notify(vm);
   } catch ( const po::error& e ) {
      fmt::println("{}", e.what());
      exit_final(1);
   }

   if ( vm.contains("help") ) {
      desc.print(std::cout);
      exit_final();
   }

   if ( vm.contains("version") ) {
      fmt::println("rcpsp solver version {}", VERSION);
      exit_final();
   }

   if ( vm.contains("output") ) {
      Config::outputFile = vm["output"].as<std::string>();
   }

   if ( vm.contains("input") ) {
      Config::inputFile = vm["input"].as<std::string>();
   }

   if ( vm.contains("tl") ) {
      Config::timeLimit = vm["tl"].as<long>();
   }

   if ( vm.contains("thl") ) {
      Config::threadLimit = vm["thl"].as<unsigned>();
   }

   if ( vm.contains("ml") ) {
      Config::memoryLimit = vm["ml"].as<long>();
   }

   if ( vm.contains("alpha") ) {
      Config::alpha = vm["alpha"].as<double>();
   }

   if ( vm.contains("method") ) {
      Config::method = parseResolutionMethod(vm["method"].as<std::string>());
   }

   if ( vm.contains("verbose") ) {
      Config::verbose = true;
   }

   if ( vm.contains("withStats") ) {
      Config::withStats = true;
   }
}

void Config::showConfig()
{
   fmt::println("\nCurrent configuration:");
   fmt::println("   {:<20}{:<15}", "Method:", method);
   fmt::println("   {:<20}{:<15}", "Input file:", inputFile);
   fmt::println("   {:<20}{:<15}", "Output file:", outputFile ? outputFile.value() : "None");
   fmt::println("   {:<20}{:<15}", "Time limit:", timeLimit);
   fmt::println("   {:<20}{:<15}", "Thread limit:", threadLimit);
   fmt::println("   {:<20}{:<15}", "Memory limit(Go):", memoryLimit);
   fmt::println("   {:<20}{:<15}", "⍺ value:", alpha);
   fmt::println("   {:<20}{:<15}", "Verbose mode:", verbose ? "Yes" : "No");
   fmt::println("   {:<20}{:<15}", "Version:", VERSION);
}

void Config::init_config()
{
   Config::threadLimit = std::max(1u, std::thread::hardware_concurrency());
}

Config::ResolutionMethod Config::parseResolutionMethod(const std::string& method)
{
   std::string m;
   std::ranges::transform(method, std::back_inserter(m), ::tolower);
   if ( m.find("ilp") != m.npos )
      return Config::ResolutionMethod::ILP;
   return Config::ResolutionMethod::None;
}