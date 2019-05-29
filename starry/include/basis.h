/**
\file basis.h
\brief Miscellaneous utilities related to basis transformations.

*/

#ifndef _STARRY_BASIS_H_
#define _STARRY_BASIS_H_

#include "utils.h"

namespace starry_theano { 
namespace basis {

using namespace starry_theano::utils;

/**
Multiply a polynomial vector/matrix by `z`.

*/
template <typename T1, typename T2> 
inline void polymulz (
    int lmax, 
    const MatrixBase<T1>& p,
    MatrixBase<T2>& pz
) {
    int n = 0;
    int lz, nz;
    bool odd1;
    pz.setZero();
    for (int l = 0; l < lmax + 1; ++l) {
        for (int m = -l; m < l + 1; ++m) {
            odd1 = (l + m) % 2 == 0 ? false : true;
            lz = l + 1;
            nz = lz * lz + lz + m;
            if (odd1) {
                pz.row(nz - 4 * lz + 2) += p.row(n);
                pz.row(nz - 2) -= p.row(n);
                pz.row(nz + 2) -= p.row(n);
            } else {
                pz.row(nz) += p.row(n);
            }
            ++n;
        }
    }
}

/**
Multiply two polynomial matrices.

*/
template <typename Derived>
inline void polymul (
    int lmax1, 
    const MatrixBase<Derived>& p1,
    int lmax2,
    const MatrixBase<Derived>& p2,
    int lmax12, 
    MatrixBase<Derived>& p1p2
) {
    bool odd1;
    int l, n;
    int n2, n1 = 0;
    RowVector<typename Derived::Scalar> fac1;
    p1p2.setZero();
    for (int l1 = 0; l1 < lmax1 + 1; ++l1) {
        for (int m1 = -l1; m1 < l1 + 1; ++m1) {
            if (p1.row(n1).any()) {
                odd1 = (l1 + m1) % 2 == 0 ? false : true;
                n2 = 0;
                for (int l2 = 0; l2 < lmax2 + 1; ++l2) {
                    if (l1 + l2 > lmax12) break;
                    for (int m2 = -l2; m2 < l2 + 1; ++m2) {
                        if (p2.row(n2).any()) {
                            l = l1 + l2;
                            n = l * l + l + m1 + m2;
                            fac1 = p1.row(n1).cwiseProduct(p2.row(n2));
                            if (odd1 && ((l2 + m2) % 2 != 0)) {
                                p1p2.row(n - 4 * l + 2) += fac1;
                                p1p2.row(n - 2) -= fac1;
                                p1p2.row(n + 2) -= fac1;
                            } else {
                                p1p2.row(n) += fac1;
                            }
                        }
                        ++n2;
                    }
                }
            }
            ++n1;
        }
    }  
}

/**
Compute the `P(z)` part of the Ylm vectors.

*/
template <typename Derived>
inline void legendre (
    int lmax, 
    MatrixBase<Derived>& M
) {
    M.setZero();
    int ip, im;
    typename Derived::Scalar term = 1, fac = 1;
    Vector<typename Derived::Scalar> col((lmax + 1) * (lmax + 1));
    for (int m = 0; m < lmax + 1; ++m) {
        // 1
        ip = m * m + 2 * m;
        im = m * m;
        M(0, ip) = fac;
        M(0, im) = fac;
        if (m < lmax) {
            // z
            ip = m * m + 4 * m + 2;
            im = m * m + 2 * m + 2;
            M(2, ip) = (2 * m + 1) * M(m * m + 2 * m, 0);
            M(2, im) = M(2, ip);
        }
        for (int l = m + 1; l < lmax + 1; ++l) {
            // Recurse
            ip = l * l + l + m;
            im = l * l + l - m;
            polymulz(lmax - 1, M.col((l - 1) * (l - 1) + l - 1 + m), col);
            M.col(ip) = (2 * l - 1) * col / (l - m);
            if (l > m + 1)
                M.col(ip) -= (l + m - 1) * 
                             M.col((l - 2) * (l - 2) + l - 2 + m) / (l - m);
            M.col(im) = M.col(ip);
        }
        fac *= -term;
        term += 2;
    }
}

/**
Compute the `theta(x, y)` term of the Ylm vectors.

*/
template <typename Derived>
inline void theta (
    int lmax, 
    MatrixBase<Derived>& M
) {
    int N = (lmax + 1) * (lmax + 1);
    typename Derived::Scalar term1, term2;
    int n1, n2, np1, np2;
    M.setZero();
    for (int m = 0; m < lmax + 1; ++m) {
        term1 = 1.0;
        term2 = m;
        for (int j = 0; j < m + 1; j += 2) {
            if (j > 0) {
                term1 *= -(m - j + 1.0) * (m - j + 2.0) / (j * (j - 1.0));
                term2 *= -(m - j) * (m - j + 1.0) / (j * (j + 1.0));
            }
            np1 = m * m + 2 * j;
            np2 = m * m + 2 * (j + 1);
            for (int l = m; l < lmax + 1; ++l) {
                n1 = l * l + l + m;
                n2 = l * l + l - m;
                M(np1, n1) = term1;
                if (np2 < N) 
                    M(np2, n2) = term2;
            }
        }
    }
}

/**
Compute the amplitudes of the Ylm vectors.

*/
template <typename Derived>
inline void amp (
    int lmax, 
    MatrixBase<Derived>& M
) {
    M.setZero();
    typename Derived::Scalar inv_root_two = sqrt(0.5);
    for (int l = 0; l < lmax + 1; ++l) {
        M.col(l * l + l).setConstant(sqrt(2 * (2 * l + 1)));
        for (int m = 1; m < l + 1; ++m) {
            M.col(l * l + l + m) = -M.col(l * l + l + m - 1) 
                                    / sqrt((l + m) * (l - m + 1));
            M.col(l * l + l - m) = M.col(l * l + l + m);
        }
        M.col(l * l + l) *= inv_root_two;
    }
    M /= (2 * root_pi<typename Derived::Scalar>());
}


/**
Compute the *dense* change of basis matrix `A1`.

*/
template <typename Derived>
inline void computeA1 (
    int lmax, 
    MatrixBase<Derived>& M,
    const typename Derived::Scalar& norm
) {
    typename Derived::PlainObject C, Z, XY;
    int N = (lmax + 1) * (lmax + 1);
    C.resize(N, N);
    Z.resize(N, N);
    XY.resize(N, N);
    amp(lmax, C);
    legendre(lmax, Z);
    theta(lmax, XY);
    polymul(lmax, Z, lmax, XY, lmax, M); 
    M = M.cwiseProduct(C);
    M *= norm;
}

/**
Compute the full change of basis matrix, `A`.

*/
template <typename Derived>
void computeA(
    int lmax, 
    const MatrixBase<Derived>& A1,
    MatrixBase<Derived>& A2, 
    MatrixBase<Derived>& A
) {
    using Scalar = typename Derived::Scalar;
    int i, n, l, m, mu, nu;
    int N = (lmax + 1) * (lmax + 1);

    // Let's compute the inverse of A2, since it's easier
    Matrix<Scalar> A2Inv = Matrix<Scalar>::Zero(N, N);
    n = 0;
    for (l = 0; l < lmax + 1; ++l) {
        for (m = -l; m < l + 1; ++m){
            mu = l - m;
            nu = l + m;
            if (nu % 2 == 0) {
                // x^(mu/2) y^(nu/2)
                A2Inv(n, n) = (mu + 2) / 2;
            } else if ((l == 1) && (m == 0)) {
                // z
                A2Inv(n, n) = 1;
            } else if ((mu == 1) && (l % 2 == 0)) {
                // x^(l-2) y z
                i = l * l + 3;
                A2Inv(i, n) = 3;
            } else if ((mu == 1) && (l % 2 == 1)) {
                // x^(l-3) z
                i = 1 + (l - 2) * (l - 2);
                A2Inv(i, n) = -1;
                // x^(l-1) z
                i = l * l + 1;
                A2Inv(i, n) = 1;
                // x^(l-3) y^2 z
                i = l * l + 5;
                A2Inv(i, n) = 4;
            } else {
                if (mu != 3) {
                    // x^((mu - 5)/2) y^((nu - 1)/2)
                    i = nu + ((mu - 4 + nu) * (mu - 4 + nu)) / 4;
                    A2Inv(i, n) = (mu - 3) / 2;
                    // x^((mu - 5)/2) y^((nu + 3)/2)
                    i = nu + 4 + ((mu + nu) * (mu + nu)) / 4;
                    A2Inv(i, n) = -(mu - 3) / 2;
                }
                // x^((mu - 1)/2) y^((nu - 1)/2)
                i = nu + (mu + nu) * (mu + nu) / 4;
                A2Inv(i, n) = -(mu + 3) / 2;
            }
            ++n;
        }
    }

    // Compute A
    Eigen::HouseholderQR<Matrix<Scalar>> solver(N, N);
    solver.compute(A2Inv);
    Matrix<Scalar> I = Matrix<Scalar>::Identity(N, N);
    A2 = solver.solve(I);
    A = solver.solve(A1);
}

/**
Compute the `r^T` phase curve solution vector.

*/
template <typename T>
void computerT (
    int lmax, 
    RowVector<T>& rT
) {
    T amp0, amp, lfac1, lfac2;
    int mu, nu;
    rT.resize((lmax + 1) * (lmax + 1));
    rT.setZero();
    amp0 = pi<T>();
    lfac1 = 1.0;
    lfac2 = 2.0 / 3.0;
    for (int l = 0; l < lmax + 1; l += 4) {
        amp = amp0;
        for (int m = 0; m < l + 1; m += 4) {
            mu = l - m;
            nu = l + m;
            rT(l * l + l + m) = amp * lfac1;
            rT(l * l + l - m) = amp * lfac1;
            if (l < lmax) {
                rT((l + 1) * (l + 1) + l + m + 1) = amp * lfac2;
                rT((l + 1) * (l + 1) + l - m + 1) = amp * lfac2;
            }
            amp *= (nu + 2.0) / (mu - 2.0);
        }
        lfac1 /= (l / 2 + 2) * (l / 2 + 3);
        lfac2 /= (l / 2 + 2.5) * (l / 2 + 3.5);
        amp0 *= 0.0625 * (l + 2) * (l + 2);
    }
    amp0 = 0.5 * pi<T>();
    lfac1 = 0.5;
    lfac2 = 4.0 / 15.0;
    for (int l = 2; l < lmax + 1; l += 4) {
        amp = amp0;
        for (int m = 2; m < l + 1; m += 4) {
            mu = l - m;
            nu = l + m;
            rT(l * l + l + m) = amp * lfac1;
            rT(l * l + l - m) = amp * lfac1;
            if (l < lmax) {
                rT((l + 1) * (l + 1) + l + m + 1) = amp * lfac2;
                rT((l + 1) * (l + 1) + l - m + 1) = amp * lfac2;
            }
            amp *= (nu + 2.0) / (mu - 2.0);
        }
        lfac1 /= (l / 2 + 2) * (l / 2 + 3);
        lfac2 /= (l / 2 + 2.5) * (l / 2 + 3.5);
        amp0 *= 0.0625 * l * (l + 4);
    }
}

/**
Compute the *dense* change of basis matrices from limb darkening coefficients
to polynomial and Green's polynomial coefficients.

*/
template <typename Derived>
void computeU(
    int lmax, 
    const MatrixBase<Derived>& A1,
    const MatrixBase<Derived>& A, 
    MatrixBase<Derived>& U1, 
    typename Derived::Scalar norm
) {
    using Scalar = typename Derived::Scalar;
    Scalar twol, amp, lfac, lchoosek, fac0, fac;
    int N = (lmax + 1) * (lmax + 1);
    Matrix<Scalar> U0;
    Matrix<Scalar> LT, YT;
    LT.setZero(lmax + 1, lmax + 1);
    YT.setZero(lmax + 1, lmax + 1);

    // Compute L^T
    for (int l = 0; l < lmax + 1; ++l) {
        lchoosek = 1;
        for (int k = 0; k < l + 1; ++k) {
            if ((k + 1) % 2 == 0)
                LT(k, l) = lchoosek;
            else
                LT(k, l) = -lchoosek;
            lchoosek *= (l - k) / (k + 1.0);
        }
    }

    // Compute Y^T
    // Even terms
    twol = 1.0;
    lfac = 1.0;
    fac0 = 1.0;
    for (int l = 0; l < lmax + 1; l += 2) {
        amp = twol * sqrt((2 * l + 1) / (4 * pi<Scalar>())) / lfac;
        lchoosek = 1;
        fac = fac0;
        for (int k = 0; k < l + 1; k += 2) {
            YT(k, l) = amp * lchoosek * fac;
            fac *= (k + l + 1.0) / (k - l + 1.0);
            lchoosek *= (l - k) * (l - k - 1) / ((k + 1.0) * (k + 2.0));
        }
        fac0 *= -0.25 * (l + 1) * (l + 1);
        lfac *= (l + 1.0) * (l + 2.0);
        twol *= 4.0;
    }
    // Odd terms
    twol = 2.0;
    lfac = 1.0;
    fac0 = 0.5;
    for (int l = 1; l < lmax + 1; l += 2) {
        amp = twol * sqrt((2 * l + 1) / (4 * pi<Scalar>())) / lfac;
        lchoosek = l;
        fac = fac0;
        for (int k = 1; k < l + 1; k += 2) {
            YT(k, l) = amp * lchoosek * fac;
            fac *= (k + l + 1.0) / (k - l + 1.0);
            lchoosek *= (l - k) * (l - k - 1) / ((k + 1.0) * (k + 2.0));
        }
        fac0 *= -0.25 * (l + 2) * l;
        lfac *= (l + 1.0) * (l + 2.0);
        twol *= 4.0;
    }

    // Compute U0
    Eigen::HouseholderQR<Matrix<Scalar>> solver(lmax + 1, lmax + 1);
    solver.compute(YT);
    U0 = solver.solve(LT);

    // Normalize it. Since we compute `U0` from the *inverse*
    // of `A1`, we must *divide* by the normalization constant
    U0 /= norm;

    // Compute U1
    Matrix<Scalar> X(N, lmax + 1);
    X.setZero();
    for (int l = 0; l < lmax + 1; ++l)
        X(l * (l + 1), l) = 1;
    U1 = A1 * (X * U0);
}


// --


/**
Basis transform matrices and operations.

*/
template <typename T>
class Basis {

public:

    const int ydeg;                                                            /**< The highest degree of the spherical harmonic map */
    const int udeg;                                                            /**< The highest degree of the limb darkening map */
    const int fdeg;                                                            /**< The highest degree of the filter map */
    const int deg;                                                             /**< The total degree of the map */
    const double norm;                                                         /**< Map normalization constant */
    Matrix<T> A1;                                                              /**< The polynomial change of basis matrix */
    Matrix<T> A1_f;                                                            /**< The polynomial change of basis matrix for the filter operator */
    Matrix<T> A1Inv;                                                           /**< The inverse of the polynomial change of basis matrix */
    Matrix<T> A2;                                                              /**< The Green's change of basis matrix */
    Matrix<T> A;                                                               /**< The full change of basis matrix */
    RowVector<T> rT;                                                           /**< The rotation solution vector */
    RowVector<T> rTA1;                                                         /**< The rotation vector in Ylm space */         
    Eigen::SparseMatrix<T> sp_U1;                                              /**< The sparse limb darkening to polynomial change of basis matrix */
    Eigen::SparseMatrix<T> sp_A1;                                              /**< The sparse version of A1 */

    // Constructor: compute the matrices
    explicit Basis(
        int ydeg,
        int udeg, 
        int fdeg,
        T norm=2.0 / root_pi<T>() 
    ) :
        ydeg(ydeg),
        udeg(udeg), 
        fdeg(fdeg),
        deg(ydeg + udeg + fdeg),
        norm(norm)
    {
        // Compute the augmented matrices
        int N = (deg + 1) * (deg + 1);
        Matrix<T> A1_(N, N);
        Matrix<T> A1Inv_, A2_, A_, U1_;
        RowVector<T> rT_, rTA1_;
        computeA1(deg, A1_, norm);
        A1Inv_ = A1_.inverse();
        computeA(deg, A1_, A2_, A_);
        computerT(deg, rT_);
        rTA1_ = rT_ * A1_;
        computeU(deg, A1_, A_, U1_, norm);
        
        // Resize to the shapes actually used in the code
        int Ny = (ydeg + 1) * (ydeg + 1);
        int Nf = (fdeg + 1) * (fdeg + 1);
        A1 = A1_.block(0, 0, Ny, Ny);
        A1_f = A1_.block(0, 0, Nf, Nf);
        A1Inv = A1Inv_;
        A2 = A2_.block(0, 0, Ny, Ny);
        A = A_;
        rT = rT_;
        rTA1 = rTA1_.segment(0, Ny);

        // Sparse matrices used by the filter operator
        sp_U1 = (U1_.block(0, 0, (udeg + 1) * (udeg + 1), udeg + 1)).sparseView();
        sp_A1 = (A1_.block(0, 0, Nf, Nf)).sparseView();

    }

};


} // namespace basis
} // namespace starry_theano
#endif