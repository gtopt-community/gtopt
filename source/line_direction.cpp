// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <limits>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <gtopt/fmap.hpp>
#include <gtopt/line_direction.hpp>
#include <gtopt/utils.hpp>

namespace gtopt::line_direction
{

namespace
{

constexpr auto npos = std::numeric_limits<std::size_t>::max();

/// Parallel circuits collapsed into one simple-graph edge.
struct Corridor
{
  std::size_t u {};
  std::size_t v {};
  std::vector<std::size_t> circuits {};
};

struct CorridorGraph
{
  std::vector<Corridor> corridors {};
  std::vector<std::size_t> corridor_of_edge {};  ///< npos for self-loops
  /// adjacency: (neighbour bus, corridor id)
  std::vector<std::vector<std::pair<std::size_t, std::size_t>>> adj {};
};

[[nodiscard]] CorridorGraph build_corridors(std::size_t n_buses,
                                            std::span<const Edge> edges)
{
  CorridorGraph g;
  g.corridor_of_edge.assign(edges.size(), npos);
  g.adj.resize(n_buses);
  flat_map<std::pair<std::size_t, std::size_t>, std::size_t> index;
  for (const auto& [ei, e] : enumerate(edges)) {
    if (e.bus_a == e.bus_b || e.bus_a >= n_buses || e.bus_b >= n_buses) {
      continue;
    }
    const auto key = std::minmax(e.bus_a, e.bus_b);
    auto it = index.find(key);
    std::size_t cid {};
    if (it == index.end()) {
      cid = g.corridors.size();
      index.emplace(key, cid);
      g.corridors.push_back(Corridor {
          .u = key.first,
          .v = key.second,
          .circuits = {},
      });
      g.adj[key.first].emplace_back(key.second, cid);
      g.adj[key.second].emplace_back(key.first, cid);
    } else {
      cid = it->second;
    }
    g.corridors[cid].circuits.push_back(static_cast<std::size_t>(ei));
    g.corridor_of_edge[static_cast<std::size_t>(ei)] = cid;
  }
  return g;
}

/// Iterative Tarjan bridge search on the collapsed simple graph.
[[nodiscard]] std::vector<bool> bridge_corridors(const CorridorGraph& g)
{
  const auto n = g.adj.size();
  std::vector<bool> is_bridge(g.corridors.size(), false);
  std::vector<std::size_t> disc(n, npos);
  std::vector<std::size_t> low(n, 0);
  std::size_t timer = 0;

  struct Frame
  {
    std::size_t bus;
    std::size_t parent_corridor;
    std::size_t next_adj;
  };
  std::vector<Frame> stack;

  for (std::size_t root = 0; root < n; ++root) {
    if (disc[root] != npos) {
      continue;
    }
    disc[root] = low[root] = timer++;
    stack.push_back(Frame {
        .bus = root,
        .parent_corridor = npos,
        .next_adj = 0,
    });
    while (!stack.empty()) {
      auto& fr = stack.back();
      const auto& nbrs = g.adj[fr.bus];
      if (fr.next_adj < nbrs.size()) {
        const auto [w, cid] = nbrs[fr.next_adj++];
        if (cid == fr.parent_corridor) {
          continue;
        }
        if (disc[w] == npos) {
          disc[w] = low[w] = timer++;
          stack.push_back(Frame {
              .bus = w,
              .parent_corridor = cid,
              .next_adj = 0,
          });
        } else {
          low[fr.bus] = std::min(low[fr.bus], disc[w]);
        }
        continue;
      }
      // Finished `fr.bus`: propagate low-link to its parent.
      const auto child = fr.bus;
      const auto pc = fr.parent_corridor;
      stack.pop_back();
      if (!stack.empty()) {
        const auto parent = stack.back().bus;
        low[parent] = std::min(low[parent], low[child]);
        if (low[child] > disc[parent]) {
          is_bridge[pc] = true;
        }
      }
    }
  }
  return is_bridge;
}

/// Per-class element counts (subtractable, unlike OR-ed flags).
struct Counts
{
  std::size_t withdraw {0};
  std::size_t inject {0};
  std::size_t both {0};

  constexpr Counts& operator+=(const Counts& o) noexcept
  {
    withdraw += o.withdraw;
    inject += o.inject;
    both += o.both;
    return *this;
  }
  [[nodiscard]] constexpr Counts operator-(const Counts& o) const noexcept
  {
    return {
        .withdraw = withdraw - o.withdraw,
        .inject = inject - o.inject,
        .both = both - o.both,
    };
  }
  [[nodiscard]] constexpr BusInjection flags() const noexcept
  {
    return {
        .withdraw = withdraw > 0,
        .inject = inject > 0,
        .both = both > 0,
    };
  }
};

[[nodiscard]] constexpr Counts to_counts(const BusInjection& b) noexcept
{
  return {
      .withdraw = b.withdraw ? 1U : 0U,
      .inject = b.inject ? 1U : 0U,
      .both = b.both ? 1U : 0U,
  };
}

/// Small union-find (path halving).
class Dsu
{
public:
  explicit Dsu(std::size_t n)
      : m_parent_(n)
  {
    for (std::size_t i = 0; i < n; ++i) {
      m_parent_[i] = i;
    }
  }
  [[nodiscard]] std::size_t find(std::size_t x) noexcept
  {
    while (m_parent_[x] != x) {
      m_parent_[x] = m_parent_[m_parent_[x]];
      x = m_parent_[x];
    }
    return x;
  }
  void unite(std::size_t a, std::size_t b) noexcept
  {
    a = find(a);
    b = find(b);
    if (a != b) {
      m_parent_[b] = a;
    }
  }

private:
  std::vector<std::size_t> m_parent_;
};

}  // namespace

std::vector<bool> find_radial(std::size_t n_buses, std::span<const Edge> edges)
{
  const auto g = build_corridors(n_buses, edges);
  const auto is_bridge = bridge_corridors(g);
  std::vector<bool> radial(edges.size(), false);
  for (const auto& [ei, cid] : enumerate(g.corridor_of_edge)) {
    if (cid != npos && is_bridge[cid]) {
      radial[static_cast<std::size_t>(ei)] = true;
    }
  }
  return radial;
}

std::vector<LineClass> classify(std::span<const BusInjection> buses,
                                std::span<const Edge> edges)
{
  const auto n = buses.size();
  std::vector<LineClass> out(edges.size());
  const auto g = build_corridors(n, edges);
  const auto is_bridge = bridge_corridors(g);

  // 2-edge-connected components: buses joined by non-bridge corridors.
  Dsu dsu(n);
  for (const auto& [cid, c] : enumerate(g.corridors)) {
    if (!is_bridge[static_cast<std::size_t>(cid)]) {
      dsu.unite(c.u, c.v);
    }
  }
  std::vector<std::size_t> comp(n, npos);
  std::size_t n_comp = 0;
  std::vector<std::size_t> root_comp(n, npos);
  for (std::size_t b = 0; b < n; ++b) {
    const auto r = dsu.find(b);
    if (root_comp[r] == npos) {
      root_comp[r] = n_comp++;
    }
    comp[b] = root_comp[r];
  }
  std::vector<Counts> own(n_comp);
  for (std::size_t b = 0; b < n; ++b) {
    own[comp[b]] += to_counts(buses[b]);
  }

  // Bridge forest over components: (neighbour comp, corridor id).
  std::vector<std::vector<std::pair<std::size_t, std::size_t>>> tree(n_comp);
  for (const auto& [cid, c] : enumerate(g.corridors)) {
    if (is_bridge[static_cast<std::size_t>(cid)]) {
      tree[comp[c.u]].emplace_back(comp[c.v], static_cast<std::size_t>(cid));
      tree[comp[c.v]].emplace_back(comp[c.u], static_cast<std::size_t>(cid));
    }
  }

  // Iterative DFS: parent comp, tree root, post-order subtree counts.
  std::vector<std::size_t> parent(n_comp, npos);
  std::vector<std::size_t> tree_root(n_comp, npos);
  std::vector<Counts> sub(own);
  std::vector<std::size_t> order;
  order.reserve(n_comp);
  for (std::size_t r = 0; r < n_comp; ++r) {
    if (tree_root[r] != npos) {
      continue;
    }
    tree_root[r] = r;
    std::vector<std::size_t> st {r};
    while (!st.empty()) {
      const auto x = st.back();
      st.pop_back();
      order.push_back(x);
      for (const auto& [y, cid] : tree[x]) {
        if (tree_root[y] == npos) {
          tree_root[y] = r;
          parent[y] = x;
          st.push_back(y);
        }
      }
    }
  }
  for (const auto node : std::views::reverse(order)) {
    if (parent[node] != npos) {
      sub[parent[node]] += sub[node];
    }
  }

  for (const auto& [cid, c] : enumerate(g.corridors)) {
    if (!is_bridge[static_cast<std::size_t>(cid)]) {
      continue;
    }
    const auto cu = comp[c.u];
    const auto cv = comp[c.v];
    // The child of the bridge in the DFS forest holds one side.
    const bool v_is_child = parent[cv] == cu;
    const auto child = v_is_child ? cv : cu;
    const auto total = sub[tree_root[child]];
    const Counts child_side = sub[child];
    const Counts other_side = total - child_side;
    const auto side_u =
        side_class((v_is_child ? other_side : child_side).flags());
    const auto side_v =
        side_class((v_is_child ? child_side : other_side).flags());
    const bool certifiable =
        c.circuits.size() == 1
        || std::ranges::all_of(
            c.circuits, [&](std::size_t ei) { return edges[ei].kvl_coupled; });
    for (const auto ei : c.circuits) {
      auto& lc = out[ei];
      lc.radial = true;
      const bool a_is_u = edges[ei].bus_a == c.u;
      lc.side_a = a_is_u ? side_u : side_v;
      lc.side_b = a_is_u ? side_v : side_u;
      lc.verdict =
          certifiable ? side_verdict(lc.side_a, lc.side_b) : Verdict::both;
    }
  }
  return out;
}

Summary summarize(std::span<const LineClass> classes) noexcept
{
  Summary s;
  s.lines = classes.size();
  for (const auto& c : classes) {
    if (!c.radial) {
      continue;
    }
    ++s.radial;
    switch (c.verdict) {
      case Verdict::ab:
      case Verdict::ba:
        ++s.one_way;
        break;
      case Verdict::dead_end:
        ++s.dead_end;
        break;
      case Verdict::both:
        ++s.mixed;
        break;
    }
  }
  return s;
}

void collect_element_refs(std::string_view expression,
                          std::string_view element_type,
                          ElementRefs& refs)
{
  const auto lower = [](char ch)
  { return static_cast<char>(std::tolower(static_cast<unsigned char>(ch))); };
  const auto is_word = [](char ch)
  {
    const auto uc = static_cast<unsigned char>(ch);
    return std::isalnum(uc) != 0 || ch == '_';
  };
  const auto n = expression.size();
  const auto m = element_type.size();
  for (std::size_t i = 0; i + m <= n; ++i) {
    bool match = true;
    for (std::size_t k = 0; k < m; ++k) {
      if (lower(expression[i + k]) != lower(element_type[k])) {
        match = false;
        break;
      }
    }
    if (!match || (i > 0 && is_word(expression[i - 1]))) {
      continue;
    }
    auto j = i + m;
    while (j < n
           && std::isspace(static_cast<unsigned char>(expression[j])) != 0)
    {
      ++j;
    }
    if (j >= n || expression[j] != '(') {
      continue;
    }
    ++j;
    // Parse a comma-separated list of quoted ids up to ')'.
    bool plain = true;
    bool any = false;
    while (j < n) {
      while (j < n
             && std::isspace(static_cast<unsigned char>(expression[j])) != 0)
      {
        ++j;
      }
      if (j < n && expression[j] == ')') {
        break;
      }
      if (j < n && (expression[j] == '"' || expression[j] == '\'')) {
        const char q = expression[j++];
        const auto start = j;
        while (j < n && expression[j] != q) {
          ++j;
        }
        refs.ids.emplace_back(expression.substr(start, j - start));
        any = true;
        if (j < n) {
          ++j;  // closing quote
        }
        while (j < n
               && std::isspace(static_cast<unsigned char>(expression[j])) != 0)
        {
          ++j;
        }
        if (j < n && expression[j] == ',') {
          ++j;
          continue;
        }
        if (j < n && expression[j] == ')') {
          break;
        }
      }
      plain = false;  // unquoted token, filter, `all`, …
      break;
    }
    if (!plain || !any) {
      refs.all = true;
    }
    i = j;
  }
}

}  // namespace gtopt::line_direction
