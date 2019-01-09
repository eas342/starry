#ifndef _STARRY_UTILS_H_
#define _STARRY_UTILS_H_

// Includes
#include <stdlib.h>
#include <iostream>
#include <cmath>
#include <Eigen/Core>
#include <Eigen/Dense>
#include <Eigen/SparseLU>
#include <unsupported/Eigen/AutoDiff>
#include <random>
#include <chrono>

// Boost support
#ifdef STARRY_ENABLE_BOOST
#include <boost/multiprecision/cpp_dec_float.hpp>
#include <boost/math/special_functions/gamma.hpp>
#endif

// Python interface via pybind11
#ifdef STARRY_ENABLE_PYTHON_INTERFACE
#include <pybind11/pybind11.h>
#include <pybind11/eigen.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
namespace py = pybind11;
#endif

//! Compiler branching optimizations
#define likely(x)                   __builtin_expect (!!(x), 1)
#define unlikely(x)                 __builtin_expect (!!(x), 0)

//! Default number of digits in multiprecision mode
#ifndef STARRY_NMULTI
#define STARRY_NMULTI               32
#endif

//! Max iterations in elliptic integrals
#ifndef STARRY_ELLIP_MAX_ITER
#define STARRY_ELLIP_MAX_ITER       200
#endif

//! Max iterations in computing the M & N integrals
#ifndef STARRY_MN_MAX_ITER
#define STARRY_MN_MAX_ITER           100
#endif

//! Cutoff value for `b` below which we reparametrize LD evaluation
#ifndef STARRY_BCUT
#define STARRY_BCUT                 1.0e-3
#endif

//! Things currently go numerically unstable in our bases for high `l`
#ifndef STARRY_MAX_LMAX
#define STARRY_MAX_LMAX             50
#endif

// If we're keeping dF/du as dF/dg, we need to increase the size of 
// the array containing the derivative by 1
#ifdef STARRY_KEEP_DFDU_AS_DFDG
#define STARRY_DFDU_DELTA	        1
#else
#define STARRY_DFDU_DELTA	        0
#endif

//! Pi
#ifndef M_PI
#define M_PI     3.14159265358979323846264338328
#endif

//! Square root of Pi
#ifndef M_SQRTPI
#define M_SQRTPI 1.77245385090551602729816748334
#endif

namespace starry2 { 
namespace utils {

//! Commonly used stuff throughout starry
using std::abs;
using std::max;
using std::isinf;


// --------------------------
// ----- Bounds checks ------
// --------------------------


#ifdef STARRY_DEBUG

// Bounds and shape checking enabled in debug mode
#define check_shape(MATRIX, ROWS, COLS)\
    if (!((MATRIX.rows() == ROWS) && (MATRIX.cols() == COLS)))\
        throw errors::ShapeError("Matrix has incorrect shape.")
#define check_cols(MATRIX, COLS)\
    if (MATRIX.cols() != COLS)\
        throw errors::ShapeError("Matrix has incorrect number of columns.") 
#define check_rows(MATRIX, ROWS)\
    if (MATRIX.rows() != ROWS)\
        throw errors::ShapeError("Matrix has incorrect number of rows.")  
#define check_bounds(INDEX, IMIN, IMAX)\
    if ((INDEX < IMIN) || (INDEX > IMAX))\
        throw errors::IndexError("Index out of bounds.")

#else

// These are compiled out!
#define check_shape(MATRIX, ROWS, COLS)  do {} while(0)
#define check_cols(MATRIX, COLS)  do {} while(0)
#define check_rows(MATRIX, ROWS)  do {} while(0)
#define check_bounds(INDEX, IMIN, IMAX)  do {} while(0)

#endif


// --------------------------
// ----- Linear Algebra -----
// --------------------------


//! Matrices
using Eigen::Ref;
using Eigen::MatrixBase;
template <typename T>
using Vector = Eigen::Matrix<T, Eigen::Dynamic, 1>;
template <typename T>
using UnitVector = Eigen::Matrix<T, 3, 1>;
template <typename T>
using RowVector = Eigen::Matrix<T, 1, Eigen::Dynamic>;
template <typename T>
using OneByOne = Eigen::Matrix<T, 1, 1>;
template <typename T>
using Matrix = Eigen::Matrix<T, Eigen::Dynamic, Eigen::Dynamic>;
template <typename T>
using RowMatrix = Eigen::Matrix<T, Eigen::Dynamic, 
                                Eigen::Dynamic, Eigen::RowMajor>;


// --------------------------
// ------- Data Types -------
// --------------------------


//! Cast away the const qualifier on a reference to
//! a MatrixBase variable. See the discussion here:
//! https://eigen.tuxfamily.org/dox/TopicFunctionTakingEigenTypes.html
#define MBCAST(x, T) const_cast<MatrixBase<T>&>(x)


//! Multiprecision datatype
#ifdef STARRY_ENABLE_BOOST
typedef boost::multiprecision::cpp_dec_float<STARRY_NMULTI> mp_backend;
typedef boost::multiprecision::number<mp_backend, 
                                      boost::multiprecision::et_off> Multi;
#else
typedef void Multi;
#endif

//! Default single-wavelength, static type
template <typename T>
struct Default 
{
    using Scalar = T;
    using YType = Vector<T>;
    using YCoeffType = OneByOne<T>;
    using UType = Vector<T>;
    using UCoeffType = OneByOne<T>;

    struct Double {
        using Scalar = double;
        using YType = Vector<double>;
        using YCoeffType = OneByOne<double>;
        using UType = Vector<double>;
        using UCoeffType = OneByOne<double>;
    };

};

//! Spectral type
template <typename T>
struct Spectral 
{
    using Scalar = T;
    using YType = Matrix<T>;
    using YCoeffType = RowVector<T>;
    using UType = Matrix<T>;
    using UCoeffType = RowVector<T>;

    struct Double {
        using Scalar = double;
        using YType = Matrix<double>;
        using YCoeffType = RowVector<double>;
        using UType = Matrix<double>;
        using UCoeffType = RowVector<double>;
    };
};

//! Temporal type
template <typename T>
struct Temporal 
{
    using Scalar = T;
    using YType = Matrix<T>;
    using YCoeffType = RowVector<T>;
    using UType = Vector<T>;
    using UCoeffType = OneByOne<T>;

    struct Double {
        using Scalar = double;
        using YType = Matrix<double>;
        using YCoeffType = RowVector<double>;
        using UType = Vector<double>;
        using UCoeffType = OneByOne<double>;
    };
};

// Some sneaky hacks to enable/disable things depending on their type
template <typename T, typename U=void>
using IsDefault = typename std::enable_if<
                    std::is_same<T, Default<typename T::Scalar>>::value, U
                  >::type;

template <typename T, typename U=void>
using IsSpectral = typename std::enable_if<
                        std::is_same<T, Spectral<typename T::Scalar>>::value, U
                   >::type;

template <typename T, typename U=void>
using IsSingleWavelength = typename std::enable_if<
                        !std::is_same<T, Spectral<typename T::Scalar>>::value, U
                   >::type;

template <typename T, typename U=void>
using IsTemporal = typename std::enable_if<
                        std::is_same<T, Temporal<typename T::Scalar>>::value, U
                   >::type;

template <typename T, typename U=void>
using IsStatic = typename std::enable_if<
                    std::is_same<T, Default<typename T::Scalar>>::value || 
                    std::is_same<T, Spectral<typename T::Scalar>>::value, U
                 >::type;

template <typename T, typename U=void>
using IsSingleColumn = typename std::enable_if<
                          std::is_same<T, Default<typename T::Scalar>>::value, U
                       >::type;

template <typename T, typename U=void>
using IsMultiColumn = typename std::enable_if<
                        std::is_same<T, Spectral<typename T::Scalar>>::value || 
                        std::is_same<T, Temporal<typename T::Scalar>>::value, U
                      >::type;

// --------------------------
// -------- Constants -------
// --------------------------


// Tag forwarding hack
template <class T> 
struct tag {};

//! Pi for current type
#ifdef STARRY_ENABLE_BOOST
template <class T> 
inline T pi(
    tag<T>
) { 
    return boost::math::constants::pi<T>(); 
}
template <class T> 
inline Eigen::AutoDiffScalar<T> pi(
    tag<Eigen::AutoDiffScalar<T>>
) {
    return boost::math::constants::pi<typename T::Scalar>();
}
template <class T> 
inline T pi() { 
    return pi(tag<T>()); 
}
#else
template <class T> 
inline T pi() { 
    return static_cast<T>(M_PI); 
}
#endif

//! Square root of pi for current type
#ifdef STARRY_ENABLE_BOOST
template <class T> 
inline T root_pi(
    tag<T>
) { 
    return boost::math::constants::root_pi<T>(); 
}
template <class T> 
inline Eigen::AutoDiffScalar<T> root_pi (
    tag<Eigen::AutoDiffScalar<T>>
) {
    return boost::math::constants::root_pi<typename T::Scalar>();
}
template <class T> 
inline T root_pi() { 
    return root_pi(tag<T>()); 
}
#else
template <class T> 
inline T root_pi() { 
    return static_cast<T>(M_SQRTPI); 
}
#endif

//! Machine precision for current type
template<class T> 
inline T mach_eps(tag<T>) { 
    return std::numeric_limits<T>::epsilon(); 
}
template<class T> 
inline Eigen::AutoDiffScalar<T> mach_eps(
    tag<Eigen::AutoDiffScalar<T>>
) {
    return std::numeric_limits<typename T::Scalar>::epsilon();
}
template<class T> 
inline T mach_eps() { 
    return mach_eps(tag<T>()); 
}


// --------------------------
// ----- Utility Funcs ------
// --------------------------


//! Check if a number is even (or doubly, triply, quadruply... even)
inline bool is_even (
    int n, 
    int ntimes=1
) {
    for (int i = 0; i < ntimes; i++) {
        if ((n % 2) != 0) return false;
        n /= 2;
    }
    return true;
}


// --------------------------
// ------ Unit Vectors ------
// --------------------------


// Some useful unit vectors
static const UnitVector<double> xhat_double({1, 0, 0});
static const UnitVector<double> yhat_double({0, 1, 0});
static const UnitVector<double> zhat_double({0, 0, 1});

//! Unit vector in the xhat direction
template <typename T> 
inline UnitVector<T> xhat (){
    return xhat_double.template cast<T>();
}

//! Unit vector in the yhat direction
template <typename T> 
inline UnitVector<T> yhat (){
    return yhat_double.template cast<T>();
}

//! Unit vector in the zhat direction
template <typename T> 
inline UnitVector<T> zhat (){
    return zhat_double.template cast<T>();
}

// Normalize a unit vector
template <typename T>
inline UnitVector<T> norm_unit(const UnitVector<T>& vec) {
    UnitVector<T> result = vec / sqrt(vec(0) * vec(0) +
                                        vec(1) * vec(1) +
                                        vec(2) * vec(2));
    return result;
}

} // namespace utils
} // namespace starry2
#endif