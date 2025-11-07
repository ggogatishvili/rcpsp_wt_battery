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

#include <boost/graph/adjacency_list.hpp>
#include "instance.h"

class PrecedenceGraph
{
   public:
      PrecedenceGraph(const Instance * const ins);

      void writeGraphviz(std::ostream& out, const std::optional<std::list<int>>& ee_tasks = std::nullopt) const;

      auto operator()() const -> std::vector<std::vector<long>>;

   private:
      typedef boost::adjacency_list< boost::listS
                                   , boost::vecS
                                   , boost::directedS
                                   , boost::property<boost::vertex_index_t, long>
                                   , boost::property<boost::edge_weight_t, long>
                                   > graph_t;

      graph_t graph;
      const Instance * const ins;
};