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

#include "Clock.h"
#include <helpers.h>

Clock::Clock()
   : running(false)
   , _timelimit(INFINITE)
{ }

Clock::Clock(double timelimit)
   : running(false)
   , _timelimit(timelimit)
{ }

void Clock::setTimelimit(double timelimit)
{
   this->_timelimit = timelimit;
}

void Clock::start()
{
   running = true;
   start_time = std::chrono::steady_clock::now();
};

void Clock::start(double timelimit)
{
   setTimelimit(timelimit);
   start();
};

void Clock::stop()
{
   running = false;
   end_time = std::chrono::steady_clock::now();
}

double Clock::elapsed() const
{
   std::chrono::steady_clock::time_point end;
   if (running)
      end = std::chrono::steady_clock::now();
   else
      end = end_time;

   return std::chrono::duration_cast<std::chrono::microseconds>(end - start_time).count() / 1000000.0;
}

bool Clock::timeout() const
{
   return _timelimit > 0 && elapsed() > _timelimit;
}

double Clock::remaining() const
{
   double ret = 0;
   if (_timelimit > 0)
      ret = _timelimit - elapsed();

   return ret > 0 ? ret : 0;
}