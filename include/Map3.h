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
class Map3
{
   public:
      inline Map3<T>();
      inline void clear();
      inline void set(int a, int b, int c, const T& value);
      inline T& get(int a, int b, int c);
      inline const T& get(int a, int b, int c) const;
      inline const T& getOrDefault(int a, int b, int c, const T&def) const;
      inline bool check(int a, int b, int c) const;

   private:
      std::map<int,std::map<int,std::map<int,T>>> myMap;
};

template<class T>
Map3<T>::Map3()
{ }

template<class T>
void Map3<T>::clear()
{
   myMap.clear();
}

template<class T>
void Map3<T>::set(int a, int b, int c, const T& value)
{
   myMap[a][b][c] = value;
}

template<class T>
T& Map3<T>::get(int a, int b, int c)
{
   return myMap[a][b][c];
}

template<class T>
const T& Map3<T>::get(int a, int b, int c) const
{
   return myMap.at(a).at(b).at(c);
}

template<class T>
const T& Map3<T>::getOrDefault(int a, int b, int c, const T& def) const
{
   return check(a, b, c)? get(a,b,c) : def;
}

template<class T>
bool Map3<T>::check(int a, int b, int c) const
{
   return CONTAINS(a, myMap) && CONTAINS(b, myMap.at(a)) && CONTAINS(c, myMap.at(a).at(b));
}