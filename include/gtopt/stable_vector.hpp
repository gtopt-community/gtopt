// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      stable_vector.hpp
 * @brief     Append-only random-access container with stable references
 *
 * `StableVector<T>` stores its elements in geometrically growing chunks
 * (64, 128, 256, … elements) that are never moved: `emplace_back` never
 * invalidates a reference, pointer or index to an existing element.
 * Random access is O(1) (a bit-width and two subtractions), and growth
 * never copies or moves the stored elements, so the peak memory of a
 * build does not double at a reallocation either.
 *
 * `LinearProblem` keeps its rows and columns here.  Element builders
 * take `auto& row = lp.row_at(i)` and keep stamping through it while
 * other code adds rows; with a `std::vector` the first reallocation left
 * those references dangling and silently dropped coefficients, so the
 * LP (and the solver result) depended on the memory layout.  The stable
 * storage makes that pattern correct by construction.
 */

#pragma once

#include <bit>
#include <compare>
#include <cstddef>
#include <iterator>
#include <stdexcept>
#include <utility>
#include <vector>

namespace gtopt
{

template<typename T>
class StableVector
{
public:
  using value_type = T;
  using size_type = std::size_t;
  using difference_type = std::ptrdiff_t;
  using reference = T&;
  using const_reference = const T&;

  /// Elements in the first chunk; chunk `c` holds `kFirst · 2^c`.
  static constexpr size_type kFirst = 64;

  template<bool Const>
  class Iterator
  {
  public:
    using iterator_category = std::random_access_iterator_tag;
    using iterator_concept = std::random_access_iterator_tag;
    using value_type = T;
    using difference_type = std::ptrdiff_t;
    using owner_t = std::conditional_t<Const, const StableVector, StableVector>;
    using reference = std::conditional_t<Const, const T&, T&>;
    using pointer = std::conditional_t<Const, const T*, T*>;

    constexpr Iterator() noexcept = default;
    constexpr Iterator(owner_t* owner, difference_type pos) noexcept
        : m_owner_(owner)
        , m_pos_(pos)
    {
    }
    /// `iterator` → `const_iterator` (a template, so never the copy
    /// constructor).
    template<bool C = Const>
      requires C
    // NOLINTNEXTLINE(google-explicit-constructor,hicpp-explicit-conversions)
    constexpr Iterator(const Iterator<false>& other) noexcept
        : m_owner_(other.m_owner_)
        , m_pos_(other.m_pos_)
    {
    }

    [[nodiscard]] constexpr reference operator*() const
    {
      return (*m_owner_)[static_cast<size_type>(m_pos_)];
    }
    [[nodiscard]] constexpr pointer operator->() const { return &**this; }
    [[nodiscard]] constexpr reference operator[](difference_type n) const
    {
      return *(*this + n);
    }

    constexpr Iterator& operator++() noexcept
    {
      ++m_pos_;
      return *this;
    }
    constexpr Iterator operator++(int) noexcept
    {
      auto tmp = *this;
      ++m_pos_;
      return tmp;
    }
    constexpr Iterator& operator--() noexcept
    {
      --m_pos_;
      return *this;
    }
    constexpr Iterator operator--(int) noexcept
    {
      auto tmp = *this;
      --m_pos_;
      return tmp;
    }
    constexpr Iterator& operator+=(difference_type n) noexcept
    {
      m_pos_ += n;
      return *this;
    }
    constexpr Iterator& operator-=(difference_type n) noexcept
    {
      m_pos_ -= n;
      return *this;
    }
    [[nodiscard]] friend constexpr Iterator operator+(
        Iterator it, difference_type n) noexcept
    {
      it += n;
      return it;
    }
    [[nodiscard]] friend constexpr Iterator operator+(difference_type n,
                                                      Iterator it) noexcept
    {
      it += n;
      return it;
    }
    [[nodiscard]] friend constexpr Iterator operator-(
        Iterator it, difference_type n) noexcept
    {
      it -= n;
      return it;
    }
    [[nodiscard]] friend constexpr difference_type operator-(
        const Iterator& a, const Iterator& b) noexcept
    {
      return a.m_pos_ - b.m_pos_;
    }
    [[nodiscard]] friend constexpr bool operator==(const Iterator& a,
                                                   const Iterator& b) noexcept
    {
      return a.m_pos_ == b.m_pos_;
    }
    [[nodiscard]] friend constexpr auto operator<=>(const Iterator& a,
                                                    const Iterator& b) noexcept
    {
      return a.m_pos_ <=> b.m_pos_;
    }

  private:
    friend class Iterator<!Const>;
    owner_t* m_owner_ {nullptr};
    difference_type m_pos_ {0};
  };

  using iterator = Iterator<false>;
  using const_iterator = Iterator<true>;

  constexpr StableVector() noexcept = default;
  constexpr StableVector(StableVector&&) noexcept = default;
  constexpr StableVector& operator=(StableVector&&) noexcept = default;
  constexpr ~StableVector() = default;

  /// Copies keep each chunk reserved at its full size, so appending to a
  /// copy never moves its elements either (a plain `std::vector` copy
  /// would shrink the last chunk's capacity to its size).
  constexpr StableVector(const StableVector& other)
      : m_size_(other.m_size_)
  {
    m_chunks_.reserve(other.m_chunks_.size());
    for (const auto& src : other.m_chunks_) {
      add_chunk();
      m_chunks_.back().assign(src.begin(), src.end());
    }
  }
  constexpr StableVector& operator=(const StableVector& other)
  {
    if (this != &other) {
      StableVector tmp(other);
      *this = std::move(tmp);
    }
    return *this;
  }

  /// Capacity hint: allocates the chunks that hold the first `n`
  /// elements up front.  Never moves an element.
  constexpr void reserve(size_type n)
  {
    while (capacity() < n) {
      add_chunk();
    }
  }

  template<typename... Args>
  constexpr reference emplace_back(Args&&... args)
  {
    if (m_size_ == capacity()) {
      add_chunk();
    }
    auto& chunk = m_chunks_[chunk_of(m_size_)];
    auto& elem = chunk.emplace_back(std::forward<Args>(args)...);
    ++m_size_;
    return elem;
  }
  constexpr void push_back(const T& value) { emplace_back(value); }
  constexpr void push_back(T&& value) { emplace_back(std::move(value)); }

  [[nodiscard]] constexpr reference operator[](size_type i) noexcept
  {
    const auto c = chunk_of(i);
    return m_chunks_[c][i - chunk_start(c)];
  }
  [[nodiscard]] constexpr const_reference operator[](size_type i) const noexcept
  {
    const auto c = chunk_of(i);
    return m_chunks_[c][i - chunk_start(c)];
  }
  [[nodiscard]] constexpr reference at(size_type i)
  {
    check(i);
    return (*this)[i];
  }
  [[nodiscard]] constexpr const_reference at(size_type i) const
  {
    check(i);
    return (*this)[i];
  }

  [[nodiscard]] constexpr reference back() noexcept
  {
    return (*this)[m_size_ - 1];
  }
  [[nodiscard]] constexpr const_reference back() const noexcept
  {
    return (*this)[m_size_ - 1];
  }

  [[nodiscard]] constexpr size_type size() const noexcept { return m_size_; }
  [[nodiscard]] constexpr bool empty() const noexcept { return m_size_ == 0; }
  [[nodiscard]] constexpr size_type capacity() const noexcept
  {
    return chunk_start(m_chunks_.size());
  }

  constexpr void clear() noexcept
  {
    m_chunks_.clear();
    m_size_ = 0;
  }

  [[nodiscard]] constexpr iterator begin() noexcept { return {this, 0}; }
  [[nodiscard]] constexpr iterator end() noexcept
  {
    return {this, static_cast<difference_type>(m_size_)};
  }
  [[nodiscard]] constexpr const_iterator begin() const noexcept
  {
    return {this, 0};
  }
  [[nodiscard]] constexpr const_iterator end() const noexcept
  {
    return {this, static_cast<difference_type>(m_size_)};
  }
  [[nodiscard]] constexpr const_iterator cbegin() const noexcept
  {
    return begin();
  }
  [[nodiscard]] constexpr const_iterator cend() const noexcept { return end(); }

private:
  /// First element index of chunk `c`: `kFirst · (2^c − 1)`.
  [[nodiscard]] static constexpr size_type chunk_start(size_type c) noexcept
  {
    return kFirst * ((size_type {1} << c) - 1);
  }
  /// Chunk holding element `i`.
  [[nodiscard]] static constexpr size_type chunk_of(size_type i) noexcept
  {
    return static_cast<size_type>(std::bit_width((i / kFirst) + 1)) - 1;
  }

  constexpr void add_chunk()
  {
    const auto c = m_chunks_.size();
    auto& chunk = m_chunks_.emplace_back();
    // Each chunk is reserved once at its final size and never grows past
    // it, so its buffer (and every reference into it) never moves; the
    // outer vector moves only the chunk handles.
    chunk.reserve(kFirst << c);
  }

  constexpr void check(size_type i) const
  {
    if (i >= m_size_) {
      throw std::out_of_range("StableVector::at");
    }
  }

  std::vector<std::vector<T>> m_chunks_ {};
  size_type m_size_ {0};
};

}  // namespace gtopt
