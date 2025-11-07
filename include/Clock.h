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

#include <chrono>


/**
* @brief A utility class for tracking elapsed time and enforcing time limits.
*
* This class provides functionality to measure elapsed time and check whether
* a specified time limit has been reached.
*/
class Clock
{
   public:

      /**
       * @brief Default constructor.
       *
       * Initializes a Clock instance without a time limit.
       */
      Clock();

      /**
       * @brief Constructor with time limit.
       *
       * @param timelimit The maximum allowed runtime in seconds.
       */
      Clock(double timelimit);

      /**
       * @brief Sets the time limit.
       *
       * @param timelimit The maximum allowed runtime in seconds.
       */
      void setTimelimit(double timelimit);

      /**
       * @brief Starts the clock.
       *
       * Begins tracking elapsed time using the previously set time limit.
       */
      void start();

      /**
       * @brief Starts the clock with a specified time limit.
       *
       * @param timelimit The maximum allowed runtime in seconds.
       */
      void start(double timelimit);

      /**
       * @brief Stops the clock.
       *
       * Stops tracking elapsed time.
       */
      void stop();

      /**
       * @brief Returns the elapsed time.
       *
       * @return The elapsed time in seconds.
       */
      double elapsed() const;

      /**
       * @brief Checks if the time limit has been reached.
       *
       * @return true if the time limit has been reached, false otherwise.
       */
      bool timeout() const;

      /**
       * @brief Returns the remaining time.
       *
       * @return The time remaining before the time limit is reached, in seconds.
       */
      double remaining() const;

	private:
	   std::chrono::steady_clock::time_point start_time, end_time;
		bool running;
		double _timelimit;
};