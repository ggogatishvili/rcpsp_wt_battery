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

#include <boost/graph/johnson_all_pairs_shortest.hpp>
#include <boost/graph/graphviz.hpp>
#include <vector>
#include "helpers.h"
#include "precedenceGraph.h"

PrecedenceGraph::PrecedenceGraph(const Instance * const ins)
   : ins(ins)
   , graph(ins->nbr_tasks())
{
   Loop(i, ins->nbr_tasks()) iterate(j, ins->tasks[i].get_successors())
   {
      boost::add_edge(i, j, -ins->pt(i), graph);
   }
}

std::vector<std::vector<long>> PrecedenceGraph::operator()() const
{
   std::vector<std::vector<long>> longest_paths_matrix(ins->nbr_tasks(), std::vector<long>(ins->nbr_tasks()));

   if ( boost::johnson_all_pairs_shortest_paths(graph, longest_paths_matrix) )
      return longest_paths_matrix;
   else
      throw std::runtime_error("Precedence constraints are corrupted: precedence graph is not a DAG!");
}

void PrecedenceGraph::writeGraphviz(std::ostream& out, const std::optional<std::list<int>>& ee_tasks) const
{
   boost::write_graphviz(out, graph,
      [&](std::ostream& os, const auto vertex)
      {
         const bool is_ee_task = ee_tasks && std::ranges::find(*ee_tasks, vertex) != ee_tasks->cend();
         fmt::format_to( std::ostream_iterator<char>(os)
                       , "[label=\"{}\"{}]"
                       , boost::get(boost::vertex_index, graph, vertex)
                       , is_ee_task ? ", style=filled, fillcolor=lightgrey" : "" );
      },
      [&](std::ostream& os, const auto edge)
      {
         fmt::format_to(std::ostream_iterator<char>(os), "[label=\"{}\"]", -boost::get(boost::edge_weight, graph, edge));
      });
}