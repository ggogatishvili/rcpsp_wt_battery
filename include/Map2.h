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

#include <map>
#include "helpers.h"

template <class T>
class Map2
{
   public:
      inline Map2<T>();
      inline void clear();
      inline void set(int a, int b, const T& value);
      inline T& i(int a, int b);
      inline const T& getOrDefault(int a, int b, const T& def) const;
      inline bool check(int a, int b);

   private:
      std::map<int,std::map<int,T>> myMap;
};

template<class T>
Map2<T>::Map2()
{ }

template<class T>
void Map2<T>::clear()
{
   myMap.clear();
}

template<class T>
void Map2<T>::set(int a, int b, const T& value)
{
   myMap[a][b] = value;
}

template<class T>
T& Map2<T>::i(int a, int b)
{
   return myMap[a][b];
}

template<class T>
const T& Map2<T>::getOrDefault(int a, int b, const T& def) const
{
   return check(a, b) ? i(a, b) : def;
}

template<class T>
bool Map2<T>::check(int a, int b)
{
   return CONTAINS(a, myMap) && CONTAINS(b, myMap[a]);
}