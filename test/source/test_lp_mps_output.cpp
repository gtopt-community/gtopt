// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/// @file test_lp_mps_output.cpp
/// @brief `--lp-file` MPS output: format selection, file contents (names,
///        sections, integer columns) on every available backend, the
///        strict / fallback policy, `--lp-compression` on the `--lp-file`
///        dump and the unchanged default `.lp` behaviour.

#include <algorithm>
#include <cctype>
#include <expected>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <regex>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/lp_file_format.hpp>
#include <gtopt/lp_matrix_options.hpp>
#include <gtopt/main_options.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/solver_registry.hpp>

#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

auto read_all(const std::filesystem::path& path) -> std::string
{
  std::ifstream in(path);
  return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

/// min 2*gen + 10*on  s.t.  gen - 5*on <= 0 ; gen >= 3 ; on integer in [0,1]
auto make_uc_lp() -> LinearProblem
{
  LinearProblem lp("mps_test");
  const auto ctx = make_block_context(
      make_uid<Scenario>(0), make_uid<Stage>(0), make_uid<Block>(0));
  const auto gen = lp.add_col(SparseCol {
      .lowb = 3.0,
      .uppb = 100.0,
      .cost = 2.0,
      .class_name = "Generator",
      .variable_name = "gen",
      .variable_uid = Uid {1},
      .context = ctx,
  });
  const auto on = lp.add_col(SparseCol {
      .lowb = 0.0,
      .uppb = 1.0,
      .cost = 10.0,
      .is_integer = true,
      .class_name = "Commitment",
      .variable_name = "status",
      .variable_uid = Uid {1},
      .context = ctx,
  });
  const auto row = lp.add_row(SparseRow {
      .uppb = 0.0,
      .class_name = "Generator",
      .constraint_name = "pmax",
      .variable_uid = Uid {1},
      .context = ctx,
  });
  lp.set_coeff(row, gen, 1.0);
  lp.set_coeff(row, on, -5.0);
  return lp;
}

auto make_interface(LinearProblem& lp, std::string_view solver = {})
    -> LinearInterface
{
  const auto flat =
      lp.flatten(make_lp_matrix_options(/*enable_names=*/true, std::nullopt));
  LinearInterface li(solver);
  li.load_flat(flat);
  return li;
}

auto available_solvers() -> std::vector<std::string>
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.available_solvers();
}

/// Solvers that accept the integer columns of make_uc_lp() (CLP is
/// LP-only and rejects them at load time).
auto mip_solvers() -> std::vector<std::string>
{
  auto solvers = available_solvers();
  std::erase_if(solvers,
                [](const std::string& s)
                { return !SolverRegistry::instance().supports_mip(s); });
  return solvers;
}

/// Backends whose native library has no writer for `format`.
auto lacks_format(std::string_view solver, LpFileFormat format) -> bool
{
  return solver == "cuopt" && format == LpFileFormat::lp;
}

auto scratch_dir(const std::string& tag) -> std::filesystem::path
{
  auto dir = std::filesystem::temp_directory_path() / ("gtopt_mps_" + tag);
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  return dir;
}

/// Column and row names found in a model file.
struct ModelNames
{
  std::set<std::string> cols;
  std::set<std::string> rows;  ///< constraints only (no objective)
};

/// Names of an MPS file (fixed or free): ROWS gives the rows (the first
/// `N` row is the objective), COLUMNS the columns (MARKER lines skipped).
auto mps_names(const std::string& text) -> ModelNames
{
  ModelNames names;
  std::istringstream in(text);
  std::string line;
  std::string section;
  std::string objective;
  while (std::getline(in, line)) {
    if (line.empty() || line.front() == '*') {
      continue;
    }
    std::istringstream tok(line);
    std::vector<std::string> f;
    for (std::string t; tok >> t;) {
      f.push_back(t);
    }
    if (f.empty()) {
      continue;
    }
    if (std::isspace(static_cast<unsigned char>(line.front())) == 0) {
      section = f.front();
      continue;
    }
    if (section == "ROWS" && f.size() >= 2) {
      if (f[0] == "N" && objective.empty()) {
        objective = f[1];
      } else if (f[0] != "N") {
        names.rows.insert(f[1]);
      }
    } else if (section == "COLUMNS" && !f.empty()
               && line.find("MARKER") == std::string::npos)
    {
      names.cols.insert(f[0]);
    }
  }
  return names;
}

/// Names of a CPLEX-LP file: the `name:` labels of the constraints in
/// the "Subject To" section are the rows; every other identifier that is
/// not a keyword or a number is a column.
auto lp_names(const std::string& text) -> ModelNames
{
  static const std::set<std::string> keywords {
      "minimize", "maximize", "min",
      "max",      "minimum",  "maximum",
      "subject",  "to",       "st",
      "s.t.",     "such",     "that",
      "bounds",   "bound",    "free",
      "generals", "general",  "gen",
      "integers", "integer",  "binaries",
      "binary",   "bin",      "end",
      "inf",      "infinity", "semi-continuous",
      "semis",    "semi",     "obj",
  };
  ModelNames names;
  std::istringstream in(text);
  std::string line;
  std::string section;
  while (std::getline(in, line)) {
    if (const auto c = line.find('\\'); c != std::string::npos) {
      line.erase(c);  // comment
    }
    auto lower = line;
    std::ranges::transform(lower,
                           lower.begin(),
                           [](unsigned char ch)
                           { return static_cast<char>(std::tolower(ch)); });
    const auto first = lower.find_first_not_of(" \t");
    if (first == std::string::npos) {
      continue;
    }
    const auto head = lower.substr(first);
    for (const char* sec : {"minimize",
                            "maximize",
                            "min",
                            "max",
                            "subject to",
                            "st",
                            "s.t.",
                            "bounds",
                            "generals",
                            "general",
                            "integers",
                            "binaries",
                            "binary",
                            "end"})
    {
      if (head == sec) {
        section = sec;
        line.clear();
      }
    }
    if (const auto colon = line.find(':'); colon != std::string::npos) {
      auto label = line.substr(0, colon);
      std::erase_if(label,
                    [](unsigned char ch) { return std::isspace(ch) != 0; });
      if (section == "subject to" || section == "st" || section == "s.t.") {
        names.rows.insert(label);
      }
      line.erase(0, colon + 1);
    }
    std::istringstream tok(line);
    for (std::string t; tok >> t;) {
      // strip operators glued to names, e.g. "-x" or "x<=".
      while (!t.empty() && std::string_view("+-<>=").contains(t.front())) {
        t.erase(0, 1);
      }
      while (!t.empty() && std::string_view("+-<>=").contains(t.back())) {
        t.pop_back();
      }
      if (t.empty()) {
        continue;
      }
      const auto ch = static_cast<unsigned char>(t.front());
      if (std::isdigit(ch) != 0 || t.front() == '.') {
        continue;  // a number
      }
      auto low = t;
      std::ranges::transform(low,
                             low.begin(),
                             [](unsigned char c2)
                             { return static_cast<char>(std::tolower(c2)); });
      if (keywords.contains(low)) {
        continue;
      }
      names.cols.insert(t);
    }
  }
  return names;
}

/// Labels the writers invent when a name is missing: c12 / r3 (gtopt's
/// own fallback), C0000012 / R0000003 (CoinUtils), x12 (SCIP), cons3.
auto generic_names(const std::set<std::string>& names)
    -> std::vector<std::string>
{
  static const std::regex generic {R"(^(c|C|r|R|x|cons|e)\d+$)"};
  std::vector<std::string> out;
  for (const auto& n : names) {
    if (std::regex_match(n, generic)) {
      out.push_back(n);
    }
  }
  return out;
}

/// Write `li` as .lp and .mps under `dir` and check that both files carry
/// the same, complete, non-generic column and row names.
void check_lp_mps_names(const LinearInterface& li,
                        const std::filesystem::path& dir,
                        std::string_view solver)
{
  const auto stem = (dir / "names").string();
  const auto mps =
      li.write_lp(stem, LpFileFormat::mps, LpFormatFallback::strict);
  REQUIRE(mps.has_value());
  const auto from_mps = mps_names(read_all(*mps));

  CHECK(from_mps.cols.size() == static_cast<size_t>(li.get_numcols()));
  CHECK(from_mps.rows.size() == static_cast<size_t>(li.get_numrows()));
  CHECK(generic_names(from_mps.cols).empty());
  CHECK(generic_names(from_mps.rows).empty());
  for (const auto& g : generic_names(from_mps.cols)) {
    MESSAGE("generic MPS column name: ", g);
  }
  for (const auto& g : generic_names(from_mps.rows)) {
    MESSAGE("generic MPS row name: ", g);
  }

  if (lacks_format(solver, LpFileFormat::lp)) {
    return;
  }
  const auto lp = li.write_lp(stem, LpFileFormat::lp, LpFormatFallback::strict);
  REQUIRE(lp.has_value());
  const auto from_lp = lp_names(read_all(*lp));
  CHECK(from_lp.rows == from_mps.rows);
  CHECK(from_lp.cols == from_mps.cols);
  for (const auto& n : from_mps.cols) {
    if (!from_lp.cols.contains(n)) {
      MESSAGE("column only in MPS: ", n);
    }
  }
  for (const auto& n : from_lp.cols) {
    if (!from_mps.cols.contains(n)) {
      MESSAGE("column only in LP: ", n);
    }
  }
  for (const auto& n : from_mps.rows) {
    if (!from_lp.rows.contains(n)) {
      MESSAGE("row only in MPS: ", n);
    }
  }
  for (const auto& n : from_lp.rows) {
    if (!from_mps.rows.contains(n)) {
      MESSAGE("row only in LP: ", n);
    }
  }
}

/// The objective constant written in a CPLEX-LP file: the numeric terms of
/// the objective that do not multiply a variable.
auto lp_objective_constant(const std::string& text) -> double
{
  std::istringstream in(text);
  std::string line;
  std::string objective;
  bool in_obj = false;
  while (std::getline(in, line)) {
    auto lower = line;
    std::ranges::transform(lower,
                           lower.begin(),
                           [](unsigned char ch)
                           { return static_cast<char>(std::tolower(ch)); });
    const auto first = lower.find_first_not_of(" \t");
    const auto head = first == std::string::npos ? "" : lower.substr(first);
    // "Minimize" (CPLEX, CoinLpIO, SCIP, MindOpt) or "min" (HiGHS).
    if (head.starts_with("minimi") || head.starts_with("maximi")
        || head == "min" || head == "max" || head == "minimum"
        || head == "maximum")
    {
      in_obj = true;
      continue;
    }
    if (head.starts_with("subject to") || head == "st" || head == "s.t.") {
      break;
    }
    if (in_obj) {
      if (const auto colon = line.find(':'); colon != std::string::npos) {
        line.erase(0, colon + 1);
      }
      objective += " " + line;
    }
  }
  // Split "+ 3 x - 2.5" into signed terms.
  std::vector<std::string> tok;
  {
    std::string cur;
    for (const char ch : objective) {
      if (ch == '+' || ch == '-') {
        // keep exponent signs ("1e+03") inside the number
        if (!cur.empty() && (cur.back() == 'e' || cur.back() == 'E')
            && std::isdigit(static_cast<unsigned char>(cur.front())) != 0)
        {
          cur += ch;
          continue;
        }
        if (!cur.empty()) {
          tok.push_back(cur);
        }
        tok.emplace_back(1, ch);
        cur.clear();
      } else if (std::isspace(static_cast<unsigned char>(ch)) != 0) {
        if (!cur.empty()) {
          tok.push_back(cur);
          cur.clear();
        }
      } else {
        cur += ch;
      }
    }
    if (!cur.empty()) {
      tok.push_back(cur);
    }
  }
  double constant = 0.0;
  double sign = 1.0;
  for (size_t k = 0; k < tok.size(); ++k) {
    const auto& t = tok[k];
    if (t == "+" || t == "-") {
      sign = t == "-" ? -1.0 : 1.0;
      continue;
    }
    const auto c0 = static_cast<unsigned char>(t.front());
    const bool numeric = std::isdigit(c0) != 0 || t.front() == '.';
    const bool next_is_name =
        k + 1 < tok.size() && tok[k + 1] != "+" && tok[k + 1] != "-";
    if (numeric && !next_is_name) {
      constant += sign * std::stod(t);
    }
    sign = 1.0;
  }
  return constant;
}

/// The objective constant written in an MPS file: minus the RHS of the
/// objective (first N) row.
auto mps_objective_constant(const std::string& text) -> double
{
  std::istringstream in(text);
  std::string line;
  std::string section;
  std::string objective;
  double constant = 0.0;
  while (std::getline(in, line)) {
    if (line.empty() || line.front() == '*') {
      continue;
    }
    std::istringstream tok(line);
    std::vector<std::string> f;
    for (std::string t; tok >> t;) {
      f.push_back(t);
    }
    if (f.empty()) {
      continue;
    }
    if (std::isspace(static_cast<unsigned char>(line.front())) == 0) {
      section = f.front();
      continue;
    }
    if (section == "ROWS" && f.size() >= 2 && f[0] == "N" && objective.empty())
    {
      objective = f[1];
    } else if (section == "RHS") {
      // "[set] row value [row value]"
      for (size_t k = f.size() % 2 == 1 ? 1 : 0; k + 1 < f.size(); k += 2) {
        if (f[k] == objective) {
          constant = -std::stod(f[k + 1]);
        }
      }
    }
  }
  return constant;
}

}  // namespace

TEST_CASE("split_lp_file_target infers format from extension")  // NOLINT
{
  {
    INFO("plain stem keeps the requested format");
    const auto t = split_lp_file_target("out/model");
    CHECK(t.stem == "out/model");
    CHECK(t.format == LpFileFormat::lp);
    CHECK(split_lp_file_target("m", LpFileFormat::mps).format
          == LpFileFormat::mps);
  }
  {
    INFO(".lp is stripped");
    const auto t = split_lp_file_target("model.lp");
    CHECK(t.stem == "model");
    CHECK(t.format == LpFileFormat::lp);
    CHECK(split_lp_file_target("model.lp", LpFileFormat::mps).format
          == LpFileFormat::mps);
  }
  {
    INFO(".mps is stripped and selects MPS");
    const auto t = split_lp_file_target("a.b/model.mps");
    CHECK(t.stem == "a.b/model");
    CHECK(t.format == LpFileFormat::mps);
  }
}

TEST_CASE("parse_lp_file_format")  // NOLINT
{
  CHECK(parse_lp_file_format("lp") == LpFileFormat::lp);
  CHECK(parse_lp_file_format("mps") == LpFileFormat::mps);
  CHECK_FALSE(parse_lp_file_format("xml").has_value());
  CHECK(lp_file_extension(LpFileFormat::lp) == ".lp");
  CHECK(lp_file_extension(LpFileFormat::mps) == ".mps");
  CHECK(other_lp_file_format(LpFileFormat::lp) == LpFileFormat::mps);
  CHECK(other_lp_file_format(LpFileFormat::mps) == LpFileFormat::lp);
  CHECK(lp_file_format_name(LpFileFormat::lp) == "lp");
  CHECK(lp_file_format_name(LpFileFormat::mps) == "mps");
  // name and parser round-trip
  for (const auto f : {LpFileFormat::lp, LpFileFormat::mps}) {
    CHECK(parse_lp_file_format(lp_file_format_name(f)) == f);
  }
  // case-sensitive, no extension dot
  CHECK_FALSE(parse_lp_file_format("MPS").has_value());
  CHECK_FALSE(parse_lp_file_format(".mps").has_value());
  CHECK_FALSE(parse_lp_file_format("").has_value());
}

TEST_CASE("write_lp default format still writes .lp")  // NOLINT
{
  const auto dir = scratch_dir("default");
  const auto stem = (dir / "model").string();
  auto lp = make_uc_lp();
  auto li = make_interface(lp);

  REQUIRE(li.write_lp(stem).has_value());
  CHECK(std::filesystem::exists(stem + ".lp"));
  CHECK_FALSE(std::filesystem::exists(stem + ".mps"));
  std::filesystem::remove_all(dir);
}

TEST_CASE("write_lp mps keeps names and integer columns")  // NOLINT
{
  for (const auto& solver : mip_solvers()) {
    CAPTURE(solver);
    const auto dir = scratch_dir("mps_" + solver);
    const auto stem = (dir / "model").string();
    auto lp = make_uc_lp();
    auto li = make_interface(lp, solver);

    // Every backend writes MPS natively.
    const auto result =
        li.write_lp(stem, LpFileFormat::mps, LpFormatFallback::strict);
    REQUIRE(result.has_value());
    CHECK(*result == stem + ".mps");
    REQUIRE(std::filesystem::exists(stem + ".mps"));
    CHECK_FALSE(std::filesystem::exists(stem + ".lp"));

    const auto text = read_all(stem + ".mps");
    CHECK(text.find("ROWS") != std::string::npos);
    CHECK(text.find("COLUMNS") != std::string::npos);
    CHECK(text.find("ENDATA") != std::string::npos);
    // Integer columns: MARKER INTORG/INTEND sections, or BV bounds for
    // binaries (CoinMpsIO).
    CHECK((text.find("MARKER") != std::string::npos
           || text.find(" BV ") != std::string::npos));
    CHECK(text.find("generator") != std::string::npos);
    CHECK(text.find("commitment") != std::string::npos);
    std::filesystem::remove_all(dir);
  }
}

TEST_CASE("write_lp lp and mps behave alike on every backend")  // NOLINT
{
  for (const auto& solver : mip_solvers()) {
    CAPTURE(solver);
    const auto dir = scratch_dir("both_" + solver);
    const auto stem = (dir / "model").string();
    auto lp = make_uc_lp();
    auto li = make_interface(lp, solver);

    for (const auto format : {LpFileFormat::lp, LpFileFormat::mps}) {
      CAPTURE(lp_file_format_name(format));
      const auto expected = stem + std::string(lp_file_extension(format));
      const auto other =
          stem + std::string(lp_file_extension(other_lp_file_format(format)));

      const auto strict = li.write_lp(stem, format, LpFormatFallback::strict);
      if (lacks_format(solver, format)) {
        // Strict: an error naming the backend and suggesting the other
        // format; nothing written.
        REQUIRE_FALSE(strict.has_value());
        CHECK(strict.error().message.find(solver) != std::string::npos);
        CHECK(strict.error().message.find("--lp-format") != std::string::npos);
        CHECK_FALSE(std::filesystem::exists(expected));

        // Fallback (diagnostic dumps): the other format, and the
        // returned path says so.
        const auto fb = li.write_lp(stem, format);
        REQUIRE(fb.has_value());
        CHECK(*fb == other);
        CHECK(std::filesystem::exists(other));
      } else {
        REQUIRE(strict.has_value());
        CHECK(*strict == expected);
        CHECK(std::filesystem::exists(expected));
      }
      std::filesystem::remove(expected);
      std::filesystem::remove(other);
    }
    std::filesystem::remove_all(dir);
  }
}

TEST_CASE("write_lp never throws when the file cannot be written")  // NOLINT
{
  namespace fs = std::filesystem;
  for (const auto& solver : mip_solvers()) {
    CAPTURE(solver);
    auto lp = make_uc_lp();
    auto li = make_interface(lp, solver);
    const auto dir = scratch_dir("unwritable_" + solver);

    for (const auto format : {LpFileFormat::lp, LpFileFormat::mps}) {
      CAPTURE(lp_file_format_name(format));
      const auto ext = std::string(lp_file_extension(format));

      {
        INFO("missing directory");
        const auto stem = (dir / "no_such_dir" / "model").string();
        for (const auto fb :
             {LpFormatFallback::strict, LpFormatFallback::other_format})
        {
          std::expected<std::string, Error> r;
          CHECK_NOTHROW(r = li.write_lp(stem, format, fb));
          CHECK_FALSE(r.has_value());
        }
        CHECK_FALSE(fs::exists(dir / "no_such_dir"));
      }
      {
        INFO("read-only directory");
        const auto ro = dir / "ro";
        fs::create_directories(ro);
        fs::permissions(ro, fs::perms::owner_read | fs::perms::owner_exec);
        // Root ignores permissions; the check is meaningful only otherwise.
        const bool enforced = [&]
        {
          std::ofstream probe(ro / "probe");
          return !probe.good();
        }();
        if (enforced) {
          std::expected<std::string, Error> r;
          CHECK_NOTHROW(r = li.write_lp((ro / "model").string(),
                                        format,
                                        LpFormatFallback::strict));
          CHECK_FALSE(r.has_value());
          CHECK_FALSE(fs::exists(ro / ("model" + ext)));
        }
        fs::permissions(ro, fs::perms::owner_all);
      }
      {
        INFO("disk full (/dev/full)");
        if (fs::exists("/dev/full")) {
          // The target is a symlink to /dev/full: open succeeds, every
          // write fails with ENOSPC.  The result must be an error (no
          // writer may report a truncated/empty model as written), and
          // nothing must be left behind.
          const auto stem = (dir / "full").string();
          std::error_code ec;
          fs::create_symlink("/dev/full", stem + ext, ec);
          REQUIRE_FALSE(ec);
          std::expected<std::string, Error> r;
          CHECK_NOTHROW(
              r = li.write_lp(stem, format, LpFormatFallback::strict));
          CHECK_FALSE(r.has_value());
          CHECK_FALSE(fs::exists(fs::symlink_status(stem + ext)));
        }
      }
    }
    fs::remove_all(dir);
  }
}

TEST_CASE("write_lp mps on a pure LP has no integer columns")  // NOLINT
{
  for (const auto& solver : available_solvers()) {
    CAPTURE(solver);
    const auto dir = scratch_dir("pure_" + solver);
    const auto stem = (dir / "model").string();
    LinearProblem lp("pure");
    const auto ctx = make_block_context(
        make_uid<Scenario>(0), make_uid<Stage>(0), make_uid<Block>(0));
    const auto c = lp.add_col(SparseCol {
        .cost = 1.0,
        .class_name = "Generator",
        .variable_name = "gen",
        .variable_uid = Uid {1},
        .context = ctx,
    });
    const auto r = lp.add_row(SparseRow {
        .lowb = 1.0,
        .class_name = "Bus",
        .constraint_name = "balance",
        .variable_uid = Uid {1},
        .context = ctx,
    });
    lp.set_coeff(r, c, 1.0);
    auto li = make_interface(lp, solver);

    REQUIRE(li.write_lp(stem, LpFileFormat::mps, LpFormatFallback::strict)
                .has_value());
    const auto text = read_all(stem + ".mps");
    CHECK(text.find("ROWS") != std::string::npos);
    CHECK(text.find("MARKER") == std::string::npos);
    CHECK(text.find(" BV ") == std::string::npos);
    std::filesystem::remove_all(dir);
  }
}

TEST_CASE("PlanningLP::write_lp compresses the dump on request")  // NOLINT
{
  for (const auto format : {LpFileFormat::lp, LpFileFormat::mps}) {
    const auto ext = std::string(lp_file_extension(format));
    CAPTURE(ext);
    const auto dir = scratch_dir("compress" + ext.substr(1));
    const auto stem = (dir / "model").string();

    {
      INFO("no codec: plain file, as before");
      auto planning = make_single_phase_planning();
      const PlanningLP planning_lp(std::move(planning));
      REQUIRE(planning_lp.write_lp(stem + ext));
      CHECK(std::filesystem::exists(stem + ext));
      CHECK_FALSE(std::filesystem::exists(stem + ext + ".gz"));
    }
    {
      INFO("gzip requested: the .lp / .mps is replaced by its .gz");
      auto planning = make_single_phase_planning();
      planning.options.lp_compression = CompressionCodec::gzip;
      const PlanningLP planning_lp(std::move(planning));
      REQUIRE(planning_lp.write_lp(stem + ext));
      CHECK(std::filesystem::exists(stem + ext + ".gz"));
      CHECK_FALSE(std::filesystem::exists(stem + ext));
    }
    std::filesystem::remove_all(dir);
  }
}

TEST_CASE("write_lp: .lp and .mps carry the same gtopt names")  // NOLINT
{
  for (const auto& solver : available_solvers()) {
    CAPTURE(solver);
    const auto dir = scratch_dir("names_" + solver);

    if (SolverRegistry::instance().supports_mip(solver)) {
      INFO("unit-commitment LinearProblem");
      auto lp = make_uc_lp();
      const auto li = make_interface(lp, solver);
      check_lp_mps_names(li, dir, solver);
    }
    {
      INFO("PlanningLP built with names, as --lp-file does");
      auto planning = make_single_phase_planning();
      const PlanningLP planning_lp(
          std::move(planning),
          make_lp_matrix_options(/*enable_names=*/true,
                                 std::nullopt,
                                 /*compute_stats=*/false,
                                 solver));
      const auto& li = planning_lp.systems().front().front().linear_interface();
      check_lp_mps_names(li, dir, solver);
    }
    std::filesystem::remove_all(dir);
  }
}

TEST_CASE("write_lp keeps the objective constant in .lp and .mps")  // NOLINT
{
  for (const auto& solver : available_solvers()) {
    CAPTURE(solver);
    const auto dir = scratch_dir("objconst_" + solver);
    const auto stem = (dir / "model").string();

    LinearProblem lp("objconst");
    const auto ctx = make_block_context(
        make_uid<Scenario>(0), make_uid<Stage>(0), make_uid<Block>(0));
    const auto c = lp.add_col(SparseCol {
        .uppb = 10.0,
        .cost = 2.0,
        .class_name = "Generator",
        .variable_name = "gen",
        .variable_uid = Uid {1},
        .context = ctx,
    });
    const auto r = lp.add_row(SparseRow {
        .lowb = 1.0,
        .class_name = "Bus",
        .constraint_name = "balance",
        .variable_uid = Uid {1},
        .context = ctx,
    });
    lp.set_coeff(r, c, 1.0);
    lp.add_obj_constant(1234.5);
    const auto flat =
        lp.flatten(make_lp_matrix_options(/*enable_names=*/true, std::nullopt));
    const auto expected = flat.obj_constant_raw;
    REQUIRE(expected != 0.0);
    LinearInterface li(solver);
    li.load_flat(flat);

    const auto mps =
        li.write_lp(stem, LpFileFormat::mps, LpFormatFallback::strict);
    REQUIRE(mps.has_value());
    CHECK(mps_objective_constant(read_all(*mps)) == doctest::Approx(expected));

    if (!lacks_format(solver, LpFileFormat::lp)) {
      const auto lpf =
          li.write_lp(stem, LpFileFormat::lp, LpFormatFallback::strict);
      REQUIRE(lpf.has_value());
      CHECK(lp_objective_constant(read_all(*lpf)) == doctest::Approx(expected));
    }
    std::filesystem::remove_all(dir);
  }
}
