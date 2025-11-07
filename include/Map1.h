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
class Map1
{
   public:
      inline Map1<T>();
      inline void clear();
      inline void set(int a, const T& value);
      inline T& get(int a);
      inline const T& getOrDefault(int a, const T& def) const;
      inline bool check(int a);

   private:
    std::map<int,T> myMap;
};

template<class T>
Map1<T>::Map1()
{ }

template<class T>
void Map1<T>::clear()
{
   myMap.clear();
}

template<class T>
void Map1<T>::set(int a, const T& value)
{
   myMap[a] = value;
}

template<class T>
T& Map1<T>::get(int a)
{
   return myMap[a];
}

template<class T>
const T& Map1<T>::getOrDefault(int a, const T& def) const
{
   return check(a) ? get(a) : def;
}

template<class T>
bool Map1<T>::check(int a)
{
   return CONTAINS(a, myMap);
}