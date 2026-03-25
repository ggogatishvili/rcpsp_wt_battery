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

#include <cstdio>
#include <fmt/base.h>
#include <iterator>
#include <algorithm>
#include <cctype>
#include <sstream>

#define FREE(X)                           if ((X) != NULL) { delete (X); (X) = NULL; }
#define iterateSet(iter, iterable)        for(std::set<int>::const_iterator iter = (iterable).begin(); iter != (iterable).end(); ++iter)
#define iterateVector(iter, iterable)     for(std::vector<int>::const_iterator iter = (iterable).begin(); iter != (iterable).end(); ++iter)
#define iterate(e, iterable)              for(auto &e : (iterable))
#define iterateCopy(e, iterable)          for(auto e : (iterable))
#define iteratePair(e, f, iterable)       for(auto &[e, f] : (iterable))
#define LoopFrom(i, from, to)             for(int i = (from); i < (to); ++i)
#define Loop(i, to)                       for(int i = 0; i < (to); ++i)

#define CONTAINS(e, container)           (container.find(e) != container.end())
#define INLIST(e, list)                  (std::find(list.begin(), list.end(), (e)) != list.end())
#define FIND(e, vec)                     (std::find(vec.begin(), vec.end(), (e)) != vec.end())

#define MAX(a, b)                ((a) > (b) ? (a) : (b))
#define MIN(a, b)                ((a) < (b) ? (a) : (b))

#define INFINITE                 std::numeric_limits<double>::infinity()
#define BIG_M                    10e8
#define MyEPS                    ((double)1e-6)

#define EQUALS(a, b)             (std::abs((a)-(b)) < MyEPS)
#define LESSER(a, b)             ((a) < ((b) - MyEPS))
#define GREATER(a, b)            ((a) > ((b) + MyEPS))
#define LESSEREQ(a, b)           ((a) < ((b) + MyEPS))
#define GREATEREQ(a, b)          ((a) > ((b) - MyEPS))
#define BETWEEN(a, lb, ub)       (GREATER(a, lb) && LESSER(a, ub))

#define TOSTRING1(a, b)          (std::string(a) + "_" + std::to_string(b)).c_str()
#define TOSTRING2(a, b, c)       (std::string(a) + "_" + std::to_string(b) + "_" + std::to_string(c)).c_str()
#define TOSTRING3(a, b, c, d)    (std::string(a) + "_" + std::to_string(b) + "_" + std::to_string(c) + "_" + std::to_string(d)).c_str()


namespace Helpers
{
   // trim from start (in place)
   inline void ltrim(std::string& s)
   {
      s.erase(s.begin(), std::find_if(s.begin(), s.end(), [](unsigned char ch)
      {
         return !std::isspace(ch);
      }));
   }

   // trim from end (in place)
   inline void rtrim(std::string& s)
   {
      s.erase(std::find_if(s.rbegin(), s.rend(), [](unsigned char ch)
      {
         return !std::isspace(ch);
      }).base(), s.end());
   }

   // trim from both ends (in place)
   inline void trim(std::string& s)
   {
      ltrim(s);
      rtrim(s);
   }

   // trim from start (copying)
   inline std::string ltrim_copy(std::string s)
   {
      ltrim(s);
      return s;
   }

   // trim from end (copying)
   inline std::string rtrim_copy(std::string s)
   {
      rtrim(s);
      return s;
   }

   // trim from both ends (copying)
   inline std::string trim_copy(std::string s)
   {
      trim(s);
      return s;
   }

   inline void toUpper(std::string &s)
   {
      std::transform(s.begin(), s.end(), s.begin(), ::toupper);
   }

   inline std::string toUpper(const std::string &s)
   {
      std::string res;
      std::ranges::transform(s, std::back_inserter(res), ::toupper);
      return res;
   }

   inline void toLower(std::string &s)
   {
      std::transform(s.begin(), s.end(), s.begin(), ::tolower);
   }

   inline std::string toLower(const std::string &s)
   {
      std::string res;
      std::ranges::transform(s, std::back_inserter(res), ::tolower);
      return res;
   }

   inline double nextDouble(std::istringstream& iss)
   {
      if ( iss.eof() )
         throw std::runtime_error("ERROR: Cannot read a double from the input!");

      double d;
      iss >> d;
      return d;
   }

   inline double nextDouble(std::istringstream& iss, double defval)
   {
      if (iss.eof())
         return defval;

      double d;
      iss >> d;
      return d;
   }

   template<typename T>
   inline T readValue(std::istringstream& iss)
   {
      if ( iss.eof() )
         throw std::runtime_error("ERROR: Cannot read the input!");

      T d;
      iss >> d;
      if ( iss.fail() )
         throw std::runtime_error("ERROR: Cannot read the input!");
      return d;
   }

   inline void setSSLine(std::istringstream& iss, const std::string& str)
   {
      iss.str(str);
      iss.clear();
      iss.seekg(0);
   }

   template<typename T>
   inline T readValue(std::istringstream& iss, const T& defval)
   {
      if ( iss.eof() )
         return defval;

      T d;
      iss >> d;
      return !iss.fail() ? d : defval;
   }

   namespace ranges
   {
      template<std::ranges::range Range, typename T>
      requires std::convertible_to<std::ranges::range_value_t<Range>, T>
      inline auto max_or_default(Range&& range, const T default_value) -> T
      {
          const auto it = std::ranges::max_element(range);
          return (it != range.end()) ? *it : default_value;
      }
   }
};

[[noreturn]] inline void exit_final(const int code = 0)
{
   exit(code);
}