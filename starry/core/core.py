# -*- coding: utf-8 -*-
from .. import config
from ..constants import *
from .. import _c_ops
from .ops import (
    sTOp,
    rTReflectedOp,
    dotROp,
    tensordotRzOp,
    FOp,
    tensordotDOp,
    spotYlmOp,
    pTOp,
    minimizeOp,
    LDPhysicalOp,
    LimbDarkOp,
    GetClOp,
    RaiseValueErrorIfOp,
)
from .utils import logger, autocompile
from .math import math
import theano
import theano.tensor as tt
import theano.sparse as ts
from theano.ifelse import ifelse
import numpy as np
from astropy import units

try:  # pragma: no cover
    # starry requires exoplanet >= v0.2.0
    from packaging import version
    import exoplanet

    if version.parse(exoplanet.__version__) < version.parse("0.2.0"):
        exoplanet = None
except ModuleNotFoundError:  # pragma: no cover
    exoplanet = None


__all__ = ["OpsYlm", "OpsLD", "OpsReflected", "OpsRV", "OpsSystem"]


class OpsYlm(object):
    """Class housing Theano operations for spherical harmonics maps."""

    def __init__(
        self, ydeg, udeg, fdeg, drorder, nw, reflected=False, **kwargs
    ):
        # Ingest kwargs
        self.ydeg = ydeg
        self.udeg = udeg
        self.fdeg = fdeg
        self.deg = ydeg + udeg + fdeg
        self.filter = (fdeg > 0) or (udeg > 0)
        self.drorder = drorder
        self.diffrot = drorder > 0
        self.nw = nw
        self._reflected = reflected

        # Instantiate the C++ Ops
        config.rootHandler.terminator = ""
        logger.info("Pre-computing some matrices... ")
        self._c_ops = _c_ops.Ops(ydeg, udeg, fdeg, drorder)
        config.rootHandler.terminator = "\n"
        logger.info("Done.")

        # Solution vectors
        self.sT = sTOp(self._c_ops.sT, self._c_ops.N)
        self.rT = tt.shape_padleft(tt.as_tensor_variable(self._c_ops.rT))
        self.rTA1 = tt.shape_padleft(tt.as_tensor_variable(self._c_ops.rTA1))

        # Change of basis matrices
        self.A = ts.as_sparse_variable(self._c_ops.A)
        self.A1 = ts.as_sparse_variable(self._c_ops.A1)
        self.A1Inv = ts.as_sparse_variable(self._c_ops.A1Inv)

        # Rotation operations
        self.tensordotRz = tensordotRzOp(self._c_ops.tensordotRz)
        self.dotR = dotROp(self._c_ops.dotR)

        # Filter
        # TODO: Make this sparse
        self.F = FOp(self._c_ops.F, self._c_ops.N, self._c_ops.Ny)

        # Differential rotation
        self.tensordotD = tensordotDOp(self._c_ops.tensordotD)

        # Misc
        self.spotYlm = spotYlmOp(self._c_ops.spotYlm, self.ydeg, self.nw)
        self.pT = pTOp(self._c_ops.pT, self.deg)
        if self.nw is None:
            if self._reflected:
                self.minimize = minimizeOp(
                    self.unweighted_intensity,
                    self.P,
                    self.ydeg,
                    self.udeg,
                    self.fdeg,
                )
            else:
                self.minimize = minimizeOp(
                    self.intensity, self.P, self.ydeg, self.udeg, self.fdeg
                )
        else:
            # TODO?
            self.minimize = None
        self.LimbDarkIsPhysical = LDPhysicalOp(_c_ops.nroots)

    @autocompile
    def limbdark_is_physical(self, u):
        """Return True if the limb darkening profile is physical."""
        return self.LimbDarkIsPhysical(u)

    @autocompile
    def get_minimum(self, y):
        """Compute the location and value of the intensity minimum."""
        return self.minimize(y)

    @autocompile
    def X(self, theta, xo, yo, zo, ro, inc, obl, u, f, alpha):
        """Compute the light curve design matrix."""
        # Determine shapes
        rows = theta.shape[0]
        cols = self.rTA1.shape[1]
        X = tt.zeros((rows, cols))

        # Compute the occultation mask
        b = tt.sqrt(xo ** 2 + yo ** 2)
        b_rot = tt.ge(b, 1.0 + ro) | tt.le(zo, 0.0) | tt.eq(ro, 0.0)
        b_occ = tt.invert(b_rot)
        i_rot = tt.arange(b.size)[b_rot]
        i_occ = tt.arange(b.size)[b_occ]

        # Compute filter operator
        if self.filter:
            F = self.F(u, f)

        # Rotation operator
        if self.filter:
            rTA1 = ts.dot(tt.dot(self.rT, F), self.A1)
        else:
            rTA1 = self.rTA1
        X = tt.set_subtensor(
            X[i_rot], self.right_project(rTA1, inc, obl, theta[i_rot], alpha)
        )

        # Occultation + rotation operator
        sT = self.sT(b[i_occ], ro)
        sTA = ts.dot(sT, self.A)
        theta_z = tt.arctan2(xo[i_occ], yo[i_occ])
        sTAR = self.tensordotRz(sTA, theta_z)
        if self.filter:
            A1InvFA1 = ts.dot(ts.dot(self.A1Inv, F), self.A1)
            sTAR = tt.dot(sTAR, A1InvFA1)
        X = tt.set_subtensor(
            X[i_occ], self.right_project(sTAR, inc, obl, theta[i_occ], alpha)
        )

        return X

    @autocompile
    def flux(self, theta, xo, yo, zo, ro, inc, obl, y, u, f, alpha):
        """Compute the light curve."""
        return tt.dot(self.X(theta, xo, yo, zo, ro, inc, obl, u, f, alpha), y)

    @autocompile
    def P(self, lat, lon):
        """Compute the pixelization matrix, no filters or illumination."""
        # Get the Cartesian points
        xpt, ypt, zpt = self.latlon_to_xyz(lat, lon)

        # Compute the polynomial basis at the point
        pT = self.pT(xpt, ypt, zpt)[:, : (self.ydeg + 1) ** 2]

        # Transform to the Ylm basis
        pTA1 = ts.dot(pT, self.A1)

        # We're done
        return pTA1

    @autocompile
    def intensity(self, lat, lon, y, u, f):
        """Compute the intensity (static, no diff. rot)."""
        # Get the Cartesian points
        xpt, ypt, zpt = self.latlon_to_xyz(lat, lon)

        # Compute the polynomial basis at the point
        pT = self.pT(xpt, ypt, zpt)

        # Transform the map to the polynomial basis
        A1y = ts.dot(self.A1, y)

        # Apply the filter
        if self.filter:
            A1y = tt.dot(self.F(u, f), A1y)

        # Dot the polynomial into the basis
        return tt.dot(pT, A1y)

    @autocompile
    def render(self, res, projection, theta, inc, obl, y, u, f, alpha):
        """Render the map on a Cartesian grid."""
        # Compute the Cartesian grid
        xyz = ifelse(
            tt.eq(projection, STARRY_RECTANGULAR_PROJECTION),
            self.compute_rect_grid(res),
            self.compute_ortho_grid(res),
        )

        # Compute the polynomial basis
        pT = self.pT(xyz[0], xyz[1], xyz[2])

        # If orthographic, rotate the map to the correct frame
        if self.nw is None:
            Ry = ifelse(
                tt.eq(projection, STARRY_ORTHOGRAPHIC_PROJECTION),
                self.left_project(
                    tt.transpose(tt.tile(y, [theta.shape[0], 1])),
                    inc,
                    obl,
                    theta,
                    alpha,
                ),
                tt.transpose(tt.tile(y, [theta.shape[0], 1])),
            )
        else:
            Ry = ifelse(
                tt.eq(projection, STARRY_ORTHOGRAPHIC_PROJECTION),
                self.left_project(
                    y, inc, obl, tt.tile(theta[0], self.nw), alpha
                ),
                y,
            )

        # Change basis to polynomials
        A1Ry = ts.dot(self.A1, Ry)

        # Apply the filter *only if orthographic*
        if self.filter:
            f0 = tt.zeros_like(f)
            f0 = tt.set_subtensor(f0[0], np.pi)
            u0 = tt.zeros_like(u)
            u0 = tt.set_subtensor(u0[0], -1.0)
            A1Ry = ifelse(
                tt.eq(projection, STARRY_ORTHOGRAPHIC_PROJECTION),
                tt.dot(self.F(u, f), A1Ry),
                tt.dot(self.F(u0, f0), A1Ry),
            )

        # Dot the polynomial into the basis
        res = tt.reshape(tt.dot(pT, A1Ry), [res, res, -1])

        # We need the shape to be (nframes, npix, npix)
        return res.dimshuffle(2, 0, 1)

    @autocompile
    def add_spot(self, y, L, amp, sigma, lat, lon):
        """Return the spherical harmonic expansion of a Gaussian spot."""
        y_new = y + self.spotYlm(amp, sigma, lat, lon)
        L_new = L * y_new[0]
        y_new /= y_new[0]
        return y_new, L_new

    def compute_ortho_grid(self, res):
        """Compute the polynomial basis on the plane of the sky."""
        dx = 2.0 / res
        y, x = tt.mgrid[-1:1:dx, -1:1:dx]
        x = tt.reshape(x, [1, -1])
        y = tt.reshape(y, [1, -1])
        z = tt.sqrt(1 - x ** 2 - y ** 2)
        return tt.concatenate((x, y, z))

    def compute_rect_grid(self, res):
        """Compute the polynomial basis on a rectangular lat/lon grid."""
        dx = np.pi / res
        lat, lon = tt.mgrid[
            -np.pi / 2 : np.pi / 2 : dx, -3 * np.pi / 2 : np.pi / 2 : 2 * dx
        ]
        x = tt.reshape(tt.cos(lat) * tt.cos(lon), [1, -1])
        y = tt.reshape(tt.cos(lat) * tt.sin(lon), [1, -1])
        z = tt.reshape(tt.sin(lat), [1, -1])
        R = self.RAxisAngle([1, 0, 0], -np.pi / 2)
        return tt.dot(R, tt.concatenate((x, y, z)))

    def right_project(self, M, inc, obl, theta, alpha, tensor_theta=True):
        r"""Apply the projection operator on the right.

        Specifically, this method returns the dot product :math:`M \cdot R`,
        where ``M`` is an input matrix and ``R`` is the Wigner rotation matrix
        that transforms a spherical harmonic coefficient vector in the
        input frame to a vector in the observer's frame.
        """
        # Trivial case
        if self.ydeg == 0:
            return M

        # Rotate to the sky frame
        # TODO: Do this in a single compound rotation
        M = self.dotR(
            self.dotR(
                self.dotR(
                    M,
                    -tt.cos(obl),
                    -tt.sin(obl),
                    math.to_tensor(0.0),
                    -(0.5 * np.pi - inc),
                ),
                math.to_tensor(0.0),
                math.to_tensor(0.0),
                math.to_tensor(1.0),
                obl,
            ),
            math.to_tensor(1.0),
            math.to_tensor(0.0),
            math.to_tensor(0.0),
            -0.5 * np.pi,
        )

        # Rotate to the correct phase
        if tensor_theta:
            M = self.tensordotRz(M, theta)
        else:
            M = self.dotR(
                M,
                math.to_tensor(0.0),
                math.to_tensor(0.0),
                math.to_tensor(1.0),
                theta,
            )

        # Rotate to the polar frame
        M = self.dotR(
            M,
            math.to_tensor(1.0),
            math.to_tensor(0.0),
            math.to_tensor(0.0),
            0.5 * np.pi,
        )

        # Apply the differential rotation
        if self.diffrot:
            if tensor_theta:
                M = self.tensordotD(M, -theta * alpha)
            else:
                raise NotImplementedError("Code this branch up if needed.")

        return M

    def left_project(self, M, inc, obl, theta, alpha, tensor_theta=True):
        r"""Apply the projection operator on the left.

        Specifically, this method returns the dot product :math:`R \cdot M`,
        where ``M`` is an input matrix and ``R`` is the Wigner rotation matrix
        that transforms a spherical harmonic coefficient vector in the
        input frame to a vector in the observer's frame.

        """
        # Trivial case
        if self.ydeg == 0:
            return M

        # Note that here we are using the fact that R . M = (M^T . R^T)^T
        MT = tt.transpose(M)

        # Apply the differential rotation
        if self.diffrot:
            if tensor_theta:
                MT = self.tensordotD(MT, theta * alpha)
            else:
                raise NotImplementedError("Code this branch up if needed.")

        # Rotate to the polar frame
        MT = self.dotR(
            MT,
            math.to_tensor(1.0),
            math.to_tensor(0.0),
            math.to_tensor(0.0),
            -0.5 * np.pi,
        )

        # Rotate to the correct phase
        if tensor_theta:
            MT = self.tensordotRz(MT, -theta)
        else:
            MT = self.dotR(
                MT,
                math.to_tensor(0.0),
                math.to_tensor(0.0),
                math.to_tensor(1.0),
                -theta,
            )

        # Rotate to the sky frame
        # TODO: Do this in a single compound rotation
        MT = self.dotR(
            self.dotR(
                self.dotR(
                    MT,
                    math.to_tensor(1.0),
                    math.to_tensor(0.0),
                    math.to_tensor(0.0),
                    0.5 * np.pi,
                ),
                math.to_tensor(0.0),
                math.to_tensor(0.0),
                math.to_tensor(1.0),
                -obl,
            ),
            -tt.cos(obl),
            -tt.sin(obl),
            math.to_tensor(0.0),
            (0.5 * np.pi - inc),
        )

        return tt.transpose(MT)

    def set_map_vector(self, vector, inds, vals):
        """Set the elements of the theano map coefficient tensor."""
        res = tt.set_subtensor(vector[inds], vals * tt.ones_like(vector[inds]))
        return res

    def latlon_to_xyz(self, lat, lon):
        """Convert lat-lon points to Cartesian points."""
        # TODO: Check that these if statements are OK
        if lat.ndim == 0:
            lat = tt.shape_padleft(lat, 1)
        if lon.ndim == 0:
            lon = tt.shape_padleft(lon, 1)
        R1 = self.RAxisAngle([1.0, 0.0, 0.0], -lat)
        R2 = self.RAxisAngle([0.0, 1.0, 0.0], lon)
        R = tt.batched_dot(R2, R1)
        xyz = tt.transpose(tt.dot(R, [0.0, 0.0, 1.0]))
        return xyz[0], xyz[1], xyz[2]

    def RAxisAngle(self, axis=[0, 1, 0], theta=0):
        """Wigner axis-angle rotation matrix."""

        def compute(axis=[0, 1, 0], theta=0):
            axis = tt.as_tensor_variable(axis)
            axis /= axis.norm(2)
            cost = tt.cos(theta)
            sint = tt.sin(theta)

            return tt.reshape(
                tt.as_tensor_variable(
                    [
                        cost + axis[0] * axis[0] * (1 - cost),
                        axis[0] * axis[1] * (1 - cost) - axis[2] * sint,
                        axis[0] * axis[2] * (1 - cost) + axis[1] * sint,
                        axis[1] * axis[0] * (1 - cost) + axis[2] * sint,
                        cost + axis[1] * axis[1] * (1 - cost),
                        axis[1] * axis[2] * (1 - cost) - axis[0] * sint,
                        axis[2] * axis[0] * (1 - cost) - axis[1] * sint,
                        axis[2] * axis[1] * (1 - cost) + axis[0] * sint,
                        cost + axis[2] * axis[2] * (1 - cost),
                    ]
                ),
                [3, 3],
            )

        # If theta is a vector, this is a tensor!
        if hasattr(theta, "ndim") and theta.ndim > 0:
            fn = lambda theta, axis: compute(axis=axis, theta=theta)
            R, _ = theano.scan(fn=fn, sequences=[theta], non_sequences=[axis])
            return R
        else:
            return compute(axis=axis, theta=theta)


class OpsLD(object):
    """Class housing Theano operations for limb-darkened maps."""

    def __init__(
        self, ydeg, udeg, fdeg, drorder, nw, reflected=False, **kwargs
    ):
        # Sanity checks
        assert ydeg == fdeg == drorder == 0
        assert reflected is False

        # Ingest kwargs
        self.udeg = udeg
        self.nw = nw

        # Set up the ops
        self.get_cl = GetClOp()
        self.limbdark = LimbDarkOp()
        self.LimbDarkIsPhysical = LDPhysicalOp(_c_ops.nroots)

    @autocompile
    def limbdark_is_physical(self, u):
        """Return True if the limb darkening profile is physical."""
        return self.LimbDarkIsPhysical(u)

    @autocompile
    def intensity(self, mu, u):
        """Compute the intensity at a set of points."""
        basis = tt.reshape(1.0 - mu, (-1, 1)) ** np.arange(self.udeg + 1)
        return -tt.dot(basis, u)

    @autocompile
    def flux(self, xo, yo, zo, ro, u):
        """Compute the light curve."""
        # Initialize flat light curve
        flux = tt.ones_like(xo)

        # Compute the occultation mask
        b = tt.sqrt(xo ** 2 + yo ** 2)
        b_occ = tt.invert(tt.ge(b, 1.0 + ro) | tt.le(zo, 0.0) | tt.eq(ro, 0.0))
        i_occ = tt.arange(b.size)[b_occ]

        # Get the Agol `c` coefficients
        c = self.get_cl(u)
        if self.udeg == 0:
            c_norm = c / (np.pi * c[0])
        else:
            c_norm = c / (np.pi * (c[0] + 2 * c[1] / 3))

        # Compute the occultation flux
        los = zo[i_occ]
        r = ro * tt.ones_like(los)
        flux = tt.set_subtensor(
            flux[i_occ], self.limbdark(c_norm, b[i_occ], r, los)[0]
        )
        return flux

    @autocompile
    def X(self, theta, xo, yo, zo, ro, inc, obl, u, f, alpha):
        """
        Convenience function for integration of limb-darkened maps
        with the ``System`` class. The design matrix for limb-darkened
        maps is just a column vector equal to the total flux, since the
        spherical harmonic coefficient vector is ``[1.0]``.

        """
        flux = self.flux(xo, yo, zo, ro, u)
        X = tt.reshape(flux, (-1, 1))
        return X

    @autocompile
    def render(self, res, projection, theta, inc, obl, y, u, f, alpha):
        """Render the map on a Cartesian grid."""
        nframes = tt.shape(theta)[0]
        image = self._render(res, u)
        return tt.tile(image, (nframes, 1, 1))

    @autocompile
    def _render(self, res, u):
        """Simplified version of `render` w/o the extra params.

        The method `render` requires a bunch of dummy params for
        compatibility with the `System` class. This method is a
        convenience method for use in the `Map` class.
        """
        # TODO: There may be a bug in Theano related to
        # tt.mgrid; I get different results depending on whether the
        # function is compiled using `theano.function()` or if it
        # is evaluated using `.eval()`. The small perturbation to `res`
        # is a temporary fix that ensures that `y` and `x` are of the
        # correct length in all cases I've tested.

        # Compute the Cartesian grid
        dx = 2.0 / (res - 0.01)
        y, x = tt.mgrid[-1:1:dx, -1:1:dx]

        x = tt.reshape(x, [1, -1])
        y = tt.reshape(y, [1, -1])
        mu = tt.sqrt(1 - x ** 2 - y ** 2)

        # Compute the intensity
        intensity = self.intensity(mu, u)

        # We need the shape to be (nframes, npix, npix)
        return tt.reshape(intensity, (1, res, res))

    def set_map_vector(self, vector, inds, vals):
        """Set the elements of the theano map coefficient tensor."""
        res = tt.set_subtensor(vector[inds], vals * tt.ones_like(vector[inds]))
        return res


class OpsRV(OpsYlm):
    """Class housing Theano operations for radial velocity maps."""

    @autocompile
    def compute_rv_filter(self, inc, obl, veq, alpha):
        """Compute the radial velocity field Ylm multiplicative filter."""
        # Define some angular quantities
        cosi = tt.cos(inc)
        sini = tt.sin(inc)
        cosl = tt.cos(obl)
        sinl = tt.sin(obl)
        A = sini * cosl
        B = sini * sinl
        C = cosi

        # Compute the Ylm expansion of the RV field
        return (
            tt.reshape(
                [
                    0,
                    veq
                    * np.sqrt(3)
                    * B
                    * (-(A ** 2) * alpha - B ** 2 * alpha - C ** 2 * alpha + 5)
                    / 15,
                    0,
                    veq
                    * np.sqrt(3)
                    * A
                    * (-(A ** 2) * alpha - B ** 2 * alpha - C ** 2 * alpha + 5)
                    / 15,
                    0,
                    0,
                    0,
                    0,
                    0,
                    veq * alpha * np.sqrt(70) * B * (3 * A ** 2 - B ** 2) / 70,
                    veq
                    * alpha
                    * 2
                    * np.sqrt(105)
                    * C
                    * (-(A ** 2) + B ** 2)
                    / 105,
                    veq
                    * alpha
                    * np.sqrt(42)
                    * B
                    * (A ** 2 + B ** 2 - 4 * C ** 2)
                    / 210,
                    0,
                    veq
                    * alpha
                    * np.sqrt(42)
                    * A
                    * (A ** 2 + B ** 2 - 4 * C ** 2)
                    / 210,
                    veq * alpha * 4 * np.sqrt(105) * A * B * C / 105,
                    veq * alpha * np.sqrt(70) * A * (A ** 2 - 3 * B ** 2) / 70,
                ],
                [-1],
            )
            * np.pi
        )

    @autocompile
    def rv(self, theta, xo, yo, zo, ro, inc, obl, y, u, veq, alpha):
        """Compute the observed radial velocity anomaly."""
        # Compute the velocity-weighted intensity
        f = self.compute_rv_filter(inc, obl, veq, alpha)
        Iv = self.flux(theta, xo, yo, zo, ro, inc, obl, y, u, f, alpha)

        # Compute the inverse of the intensity
        f0 = tt.zeros_like(f)
        f0 = tt.set_subtensor(f0[0], np.pi)
        I = self.flux(theta, xo, yo, zo, ro, inc, obl, y, u, f0, alpha)
        invI = tt.ones((1,)) / I
        invI = tt.where(tt.isinf(invI), 0.0, invI)

        # The RV signal is just the product
        return Iv * invI


class OpsReflected(OpsYlm):
    """Class housing Theano operations for reflected light maps."""

    def __init__(self, *args, **kwargs):
        super(OpsReflected, self).__init__(*args, reflected=True, **kwargs)
        self.rT = rTReflectedOp(self._c_ops.rTReflected, self._c_ops.N)
        self.A1Big = ts.as_sparse_variable(self._c_ops.A1Big)

    @autocompile
    def intensity(self, lat, lon, y, u, f, xo, yo, zo):
        """Compute the intensity at a series of lat-lon points on the surface."""
        # Get the Cartesian points
        xpt, ypt, zpt = self.latlon_to_xyz(lat, lon)

        # Compute the polynomial basis at the point
        pT = self.pT(xpt, ypt, zpt)

        # Transform the map to the polynomial basis
        A1y = ts.dot(self.A1, y)

        # Apply the filter
        A1y = tt.dot(self.F(u, f), A1y)

        # Dot the polynomial into the basis
        intensity = tt.shape_padright(tt.dot(pT, A1y))

        # Weight the intensity by the illumination
        xyz = tt.concatenate(
            (
                tt.reshape(xpt, [1, -1]),
                tt.reshape(ypt, [1, -1]),
                tt.reshape(zpt, [1, -1]),
            )
        )
        I = self.compute_illumination(xyz, xo, yo, zo)

        # Add an extra dimension for the wavelength
        if self.nw is not None:
            I = tt.shape_padaxis(I, 1)

        intensity = tt.switch(tt.isnan(intensity), intensity, intensity * I)
        return intensity

    @autocompile
    def unweighted_intensity(self, lat, lon, y, u, f):
        """Compute the intensity in the absence of an illumination source."""
        # Get the Cartesian points
        xpt, ypt, zpt = self.latlon_to_xyz(lat, lon)

        # Compute the polynomial basis at the point
        pT = self.pT(xpt, ypt, zpt)

        # Transform the map to the polynomial basis
        A1y = ts.dot(self.A1, y)

        # Apply the filter
        if self.filter:
            A1y = tt.dot(self.F(u, f), A1y)

        # Dot the polynomial into the basis
        return tt.dot(pT, A1y)

    @autocompile
    def X(self, theta, xo, yo, zo, ro, inc, obl, u, f, alpha):
        """Compute the light curve design matrix."""
        # Determine shapes
        rows = theta.shape[0]
        cols = (self.ydeg + 1) ** 2

        # Compute the occultation mask
        b = tt.sqrt(xo ** 2 + yo ** 2)
        b_rot = tt.ge(b, 1.0 + ro) | tt.le(zo, 0.0) | tt.eq(ro, 0.0)
        b_occ = tt.invert(b_rot)
        i_occ = tt.arange(b.size)[b_occ]

        # Compute the semi-minor axis of the terminator
        # and the reflectance integrals
        r2 = xo ** 2 + yo ** 2 + zo ** 2
        bterm = -zo / tt.sqrt(r2)
        rT = self.rT(bterm)

        # Transform to Ylms and rotate on the sky plane
        rTA1 = ts.dot(rT, self.A1Big)
        theta_z = tt.arctan2(xo, yo)
        rTA1Rz = self.tensordotRz(rTA1, theta_z)

        # Apply limb darkening?
        F = self.F(u, f)
        A1InvFA1 = ts.dot(ts.dot(self.A1Inv, F), self.A1)
        rTA1Rz = tt.dot(rTA1Rz, A1InvFA1)

        # Rotate to the correct phase
        X = self.right_project(rTA1Rz, inc, obl, theta, alpha)

        # Weight by the distance to the source
        # The factor of 2/3 ensures that the flux from a uniform map
        # with unit amplitude seen at noon is unity.
        X /= (2.0 / 3.0) * tt.shape_padright(r2)

        # TODO: Implement occultations in reflected light
        # Throw error if there's an occultation
        X = X + RaiseValueErrorIfOp(
            "Occultations in reflected light not yet implemented."
        )(b_occ.any())

        # We're done
        return X

    @autocompile
    def flux(self, theta, xo, yo, zo, ro, inc, obl, y, u, f, alpha):
        """Compute the reflected light curve."""
        return tt.dot(self.X(theta, xo, yo, zo, ro, inc, obl, u, f, alpha), y)

    @autocompile
    def render(
        self,
        res,
        projection,
        illuminate,
        theta,
        inc,
        obl,
        y,
        u,
        f,
        alpha,
        xo,
        yo,
        zo,
    ):
        """Render the map on a Cartesian grid."""
        # Compute the Cartesian grid
        xyz = ifelse(
            tt.eq(projection, STARRY_RECTANGULAR_PROJECTION),
            self.compute_rect_grid(res),
            self.compute_ortho_grid(res),
        )

        # Compute the polynomial basis
        pT = self.pT(xyz[0], xyz[1], xyz[2])

        # If orthographic, rotate the map to the correct frame
        if self.nw is None:
            Ry = ifelse(
                tt.eq(projection, STARRY_ORTHOGRAPHIC_PROJECTION),
                self.left_project(
                    tt.transpose(tt.tile(y, [theta.shape[0], 1])),
                    inc,
                    obl,
                    theta,
                    alpha,
                ),
                tt.transpose(tt.tile(y, [theta.shape[0], 1])),
            )
        else:
            Ry = ifelse(
                tt.eq(projection, STARRY_ORTHOGRAPHIC_PROJECTION),
                self.left_project(
                    y, inc, obl, tt.tile(theta[0], self.nw), alpha
                ),
                y,
            )

        # Transform to polynomials
        A1Ry = ts.dot(self.A1, Ry)

        # Apply the filter *only if orthographic*
        f0 = tt.zeros_like(f)
        f0 = tt.set_subtensor(f0[0], np.pi)
        u0 = tt.zeros_like(u)
        u0 = tt.set_subtensor(u0[0], -1.0)
        A1Ry = ifelse(
            tt.eq(projection, STARRY_ORTHOGRAPHIC_PROJECTION),
            tt.dot(self.F(u, f), A1Ry),
            tt.dot(self.F(u0, f0), A1Ry),
        )

        # Dot the polynomial into the basis
        image = tt.dot(pT, A1Ry)

        # Compute the illumination profile
        I = self.compute_illumination(xyz, xo, yo, zo)

        # Add an extra dimension for the wavelength
        if self.nw is not None:
            I = tt.repeat(I, self.nw, 1)

        # Weight the image by the illumination
        image = ifelse(
            illuminate, tt.switch(tt.isnan(image), image, image * I), image
        )

        # We need the shape to be (nframes, npix, npix)
        return tt.reshape(image, [res, res, -1]).dimshuffle(2, 0, 1)

    def compute_illumination(self, xyz, xo, yo, zo):
        """Compute the illumination profile when rendering maps."""
        r2 = xo ** 2 + yo ** 2 + zo ** 2
        b = -zo / tt.sqrt(r2)  # semi-minor axis of terminator
        invsr = 1.0 / tt.sqrt(xo ** 2 + yo ** 2)
        cosw = yo * invsr
        sinw = -xo * invsr
        xrot = (
            tt.shape_padright(xyz[0]) * cosw + tt.shape_padright(xyz[1]) * sinw
        )
        yrot = (
            -tt.shape_padright(xyz[0]) * sinw
            + tt.shape_padright(xyz[1]) * cosw
        )
        I = tt.sqrt(1.0 - b ** 2) * yrot - b * tt.shape_padright(xyz[2])
        I = tt.switch(
            tt.eq(tt.abs_(b), 1.0),
            tt.switch(
                tt.eq(b, 1.0),
                tt.zeros_like(I),  # midnight
                tt.shape_padright(xyz[2]),  # noon
            ),
            I,
        )
        I = tt.switch(tt.gt(I, 0.0), I, tt.zeros_like(I))  # set night to zero

        # Weight by the distance to the source
        # The factor of 2/3 ensures that the flux from a uniform map
        # with unit amplitude seen at noon is unity.
        I /= (2.0 / 3.0) * tt.shape_padleft(r2)
        return I


class OpsSystem(object):
    """Class housing ops for modeling Keplerian systems."""

    def __init__(
        self,
        primary,
        secondaries,
        reflected=False,
        rv=False,
        light_delay=False,
        texp=None,
        oversample=7,
        order=0,
    ):
        # System members
        self.primary = primary
        self.secondaries = secondaries
        self._reflected = reflected
        self._rv = rv
        self.nw = self.primary._map.nw
        self.light_delay = light_delay
        self.texp = texp
        self.oversample = oversample
        self.order = order

        # Require exoplanet
        assert exoplanet is not None, "This class requires exoplanet >= 0.2.0."

    @autocompile
    def position(
        self,
        t,
        pri_m,
        pri_t0,
        sec_m,
        sec_t0,
        sec_porb,
        sec_ecc,
        sec_w,
        sec_Omega,
        sec_iorb,
    ):
        """Compute the Cartesian positions of all bodies."""
        orbit = exoplanet.orbits.KeplerianOrbit(
            period=sec_porb,
            t0=sec_t0,
            incl=sec_iorb,
            ecc=sec_ecc,
            omega=sec_w,
            Omega=sec_Omega,
            m_planet=sec_m,
            m_star=pri_m,
            r_star=1.0,  # doesn't matter
        )
        # Position of the primary
        if len(self.secondaries) > 1:
            x_pri, y_pri, z_pri = tt.sum(orbit.get_star_position(t), axis=-1)
        else:
            x_pri, y_pri, z_pri = orbit.get_star_position(t)

        # Positions of the secondaries
        try:
            x_sec, y_sec, z_sec = orbit.get_planet_position(
                t, light_delay=self.light_delay
            )
        except TypeError:
            if self.light_delay:
                logger.warn(
                    "This version of `exoplanet` does not model light delays."
                )
            x_sec, y_sec, z_sec = orbit.get_planet_position(t)

        # Concatenate them
        x = tt.transpose(tt.concatenate((x_pri, x_sec), axis=-1))
        y = tt.transpose(tt.concatenate((y_pri, y_sec), axis=-1))
        z = tt.transpose(tt.concatenate((z_pri, z_sec), axis=-1))

        return x, y, z

    @autocompile
    def X(
        self,
        t,
        pri_r,
        pri_m,
        pri_prot,
        pri_t0,
        pri_theta0,
        pri_L,
        pri_inc,
        pri_obl,
        pri_u,
        pri_f,
        pri_alpha,
        sec_r,
        sec_m,
        sec_prot,
        sec_t0,
        sec_theta0,
        sec_porb,
        sec_ecc,
        sec_w,
        sec_Omega,
        sec_iorb,
        sec_L,
        sec_inc,
        sec_obl,
        sec_u,
        sec_f,
        sec_alpha,
    ):
        """Compute the system light curve design matrix."""
        # Exposure time integration?
        if self.texp != 0.0:

            texp = tt.as_tensor_variable(self.texp)
            oversample = int(self.oversample)
            oversample += 1 - oversample % 2
            stencil = np.ones(oversample)

            # Construct the exposure time integration stencil
            if self.order == 0:
                dt = np.linspace(-0.5, 0.5, 2 * oversample + 1)[1:-1:2]
            elif self.order == 1:
                dt = np.linspace(-0.5, 0.5, oversample)
                stencil[1:-1] = 2
            elif self.order == 2:
                dt = np.linspace(-0.5, 0.5, oversample)
                stencil[1:-1:2] = 4
                stencil[2:-1:2] = 2
            else:
                raise ValueError("Parameter `order` must be <= 2")
            stencil /= np.sum(stencil)

            if texp.ndim == 0:
                dt = texp * dt
            else:
                dt = tt.shape_padright(texp) * dt
            t = tt.shape_padright(t) + dt
            t = tt.reshape(t, (-1,))

        # Compute the relative positions of all bodies
        orbit = exoplanet.orbits.KeplerianOrbit(
            period=sec_porb,
            t0=sec_t0,
            incl=sec_iorb,
            ecc=sec_ecc,
            omega=sec_w,
            Omega=sec_Omega,
            m_planet=sec_m,
            m_star=pri_m,
            r_star=pri_r,
        )
        try:
            x, y, z = orbit.get_relative_position(
                t, light_delay=self.light_delay
            )
        except TypeError:
            if self.light_delay:
                logger.warn(
                    "This version of `exoplanet` does not model light delays."
                )
            x, y, z = orbit.get_relative_position(t)

        # Get all rotational phases
        pri_prot = ifelse(
            tt.eq(pri_prot, 0.0), math.to_tensor(np.inf), pri_prot
        )
        theta_pri = (2 * np.pi) / pri_prot * (t - pri_t0) + pri_theta0
        sec_prot = tt.switch(
            tt.eq(sec_prot, 0.0), math.to_tensor(np.inf), sec_prot
        )
        theta_sec = (2 * np.pi) / tt.shape_padright(sec_prot) * (
            tt.shape_padleft(t) - tt.shape_padright(sec_t0)
        ) + tt.shape_padright(sec_theta0)

        # Compute all the phase curves
        phase_pri = pri_L * self.primary.map.ops.X(
            theta_pri,
            tt.zeros_like(t),
            tt.zeros_like(t),
            tt.zeros_like(t),
            math.to_tensor(0.0),
            pri_inc,
            pri_obl,
            pri_u,
            pri_f,
            pri_alpha,
        )
        phase_sec = tt.as_tensor_variable(
            [
                sec_L[i]
                * sec.map.ops.X(
                    theta_sec[i],
                    -x[:, i],
                    -y[:, i],
                    -z[:, i],
                    math.to_tensor(0.0),  # occultor of zero radius
                    sec_inc[i],
                    sec_obl[i],
                    sec_u[i],
                    sec_f[i],
                    sec_alpha[i],
                )
                for i, sec in enumerate(self.secondaries)
            ]
        )

        # Compute any occultations
        occ_pri = tt.zeros_like(phase_pri)
        occ_sec = tt.zeros_like(phase_sec)

        # Compute the period if we were given a semi-major axis
        sec_porb = tt.switch(
            tt.eq(sec_porb, 0.0),
            (G_grav * (pri_m + sec_m) * sec_porb ** 2 / (4 * np.pi ** 2))
            ** (1.0 / 3),
            sec_porb,
        )

        # Compute transits across the primary
        for i, _ in enumerate(self.secondaries):
            xo = x[:, i] / pri_r
            yo = y[:, i] / pri_r
            zo = z[:, i] / pri_r
            ro = sec_r[i] / pri_r
            b = tt.sqrt(xo ** 2 + yo ** 2)
            b_occ = tt.invert(
                tt.ge(b, 1.0 + ro) | tt.le(zo, 0.0) | tt.eq(ro, 0.0)
            )
            idx = tt.arange(b.shape[0])[b_occ]
            occ_pri = tt.set_subtensor(
                occ_pri[idx],
                occ_pri[idx]
                + pri_L
                * self.primary.map.ops.X(
                    theta_pri[idx],
                    xo[idx],
                    yo[idx],
                    zo[idx],
                    ro,
                    pri_inc,
                    pri_obl,
                    pri_u,
                    pri_f,
                    pri_alpha,
                )
                - phase_pri[idx],
            )

        # Compute occultations by the primary
        for i, sec in enumerate(self.secondaries):
            xo = -x[:, i] / sec_r[i]
            yo = -y[:, i] / sec_r[i]
            zo = -z[:, i] / sec_r[i]
            ro = pri_r / sec_r[i]
            b = tt.sqrt(xo ** 2 + yo ** 2)
            b_occ = tt.invert(
                tt.ge(b, 1.0 + ro) | tt.le(zo, 0.0) | tt.eq(ro, 0.0)
            )
            idx = tt.arange(b.shape[0])[b_occ]
            occ_sec = tt.set_subtensor(
                occ_sec[i, idx],
                occ_sec[i, idx]
                + sec_L[i]
                * sec.map.ops.X(
                    theta_sec[i, idx],
                    xo[idx],
                    yo[idx],
                    zo[idx],
                    ro,
                    sec_inc[i],
                    sec_obl[i],
                    sec_u[i],
                    sec_f[i],
                    sec_alpha[i],
                )
                - phase_sec[i, idx],
            )

        # Compute secondary-secondary occultations
        for i, sec in enumerate(self.secondaries):
            for j, _ in enumerate(self.secondaries):
                if i == j:
                    continue
                xo = (-x[:, i] + x[:, j]) / sec_r[i]
                yo = (-y[:, i] + y[:, j]) / sec_r[i]
                zo = (-z[:, i] + z[:, j]) / sec_r[i]
                ro = sec_r[j] / sec_r[i]
                b = tt.sqrt(xo ** 2 + yo ** 2)
                b_occ = tt.invert(
                    tt.ge(b, 1.0 + ro) | tt.le(zo, 0.0) | tt.eq(ro, 0.0)
                )
                idx = tt.arange(b.shape[0])[b_occ]
                occ_sec = tt.set_subtensor(
                    occ_sec[i, idx],
                    occ_sec[i, idx]
                    + sec_L[i]
                    * sec.map.ops.X(
                        theta_sec[i, idx],
                        xo[idx],
                        yo[idx],
                        zo[idx],
                        ro,
                        sec_inc[i],
                        sec_obl[i],
                        sec_u[i],
                        sec_f[i],
                        sec_alpha[i],
                    )
                    - phase_sec[i, idx],
                )

                # NOTE: Not implemented in reflected light
                # Throw error if there's an occultation in reflected light
                if self._reflected:
                    occ_sec = occ_sec + RaiseValueErrorIfOp(
                        "Secondary-secondary occultations in reflected "
                        + "light not implemented."
                    )(b_occ.any())

        # Concatenate the design matrices
        X_pri = phase_pri + occ_pri
        X_sec = phase_sec + occ_sec
        X_sec = tt.reshape(tt.swapaxes(X_sec, 0, 1), (X_sec.shape[1], -1))
        X = tt.horizontal_stack(X_pri, X_sec)

        # Sum and return
        if self.texp == 0.0:

            return X

        else:

            stencil = tt.shape_padright(tt.shape_padleft(stencil, 1), 1)
            return tt.sum(
                stencil * tt.reshape(X, (-1, self.oversample, X.shape[1])),
                axis=1,
            )

    @autocompile
    def flux(
        self,
        t,
        pri_r,
        pri_m,
        pri_prot,
        pri_t0,
        pri_theta0,
        pri_L,
        pri_inc,
        pri_obl,
        pri_y,
        pri_u,
        pri_f,
        pri_alpha,
        sec_r,
        sec_m,
        sec_prot,
        sec_t0,
        sec_theta0,
        sec_porb,
        sec_ecc,
        sec_w,
        sec_Omega,
        sec_iorb,
        sec_L,
        sec_inc,
        sec_obl,
        sec_y,
        sec_u,
        sec_f,
        sec_alpha,
    ):
        """Compute the full system light curve."""
        X = self.X(
            t,
            pri_r,
            pri_m,
            pri_prot,
            pri_t0,
            pri_theta0,
            pri_L,
            pri_inc,
            pri_obl,
            pri_u,
            pri_f,
            pri_alpha,
            sec_r,
            sec_m,
            sec_prot,
            sec_t0,
            sec_theta0,
            sec_porb,
            sec_ecc,
            sec_w,
            sec_Omega,
            sec_iorb,
            sec_L,
            sec_inc,
            sec_obl,
            sec_u,
            sec_f,
            sec_alpha,
        )
        y = tt.concatenate((pri_y, tt.reshape(sec_y, (-1,))))
        return tt.dot(X, y)

    @autocompile
    def rv(
        self,
        t,
        pri_r,
        pri_m,
        pri_prot,
        pri_t0,
        pri_theta0,
        pri_L,
        pri_inc,
        pri_obl,
        pri_y,
        pri_u,
        pri_alpha,
        pri_veq,
        sec_r,
        sec_m,
        sec_prot,
        sec_t0,
        sec_theta0,
        sec_porb,
        sec_ecc,
        sec_w,
        sec_Omega,
        sec_iorb,
        sec_L,
        sec_inc,
        sec_obl,
        sec_y,
        sec_u,
        sec_alpha,
        sec_veq,
        keplerian,
    ):
        """Compute the observed system radial velocity (RV maps only)."""
        # TODO: This method is currently very inefficient, as it
        # calls `X` twice per call and instantiates an `orbit`
        # instance up to three separate times per call. We should
        # re-code the logic from `X()` in here to optimize it.

        # Compute the RV filter
        pri_f = self.primary.map.ops.compute_rv_filter(
            pri_inc, pri_obl, pri_veq, pri_alpha
        )
        sec_f = tt.as_tensor_variable(
            [
                sec.map.ops.compute_rv_filter(
                    sec_inc[k], sec_obl[k], sec_veq[k], sec_alpha[k]
                )
                for k, sec in enumerate(self.secondaries)
            ]
        )

        # Compute the identity filter
        pri_f0 = tt.zeros_like(pri_f)
        pri_f0 = tt.set_subtensor(pri_f0[0], np.pi)
        sec_f0 = tt.as_tensor_variable([pri_f0 for sec in self.secondaries])

        # Compute the two design matrices
        X = self.X(
            t,
            pri_r,
            pri_m,
            pri_prot,
            pri_t0,
            pri_theta0,
            pri_L,
            pri_inc,
            pri_obl,
            pri_u,
            pri_f,
            pri_alpha,
            sec_r,
            sec_m,
            sec_prot,
            sec_t0,
            sec_theta0,
            sec_porb,
            sec_ecc,
            sec_w,
            sec_Omega,
            sec_iorb,
            sec_L,
            sec_inc,
            sec_obl,
            sec_u,
            sec_f,
            sec_alpha,
        )

        X0 = self.X(
            t,
            pri_r,
            pri_m,
            pri_prot,
            pri_t0,
            pri_theta0,
            pri_L,
            pri_inc,
            pri_obl,
            pri_u,
            pri_f0,
            pri_alpha,
            sec_r,
            sec_m,
            sec_prot,
            sec_t0,
            sec_theta0,
            sec_porb,
            sec_ecc,
            sec_w,
            sec_Omega,
            sec_iorb,
            sec_L,
            sec_inc,
            sec_obl,
            sec_u,
            sec_f0,
            sec_alpha,
        )

        # Get the indices of X corresponding to each body
        pri_inds = np.arange(0, self.primary.map.Ny, dtype=int)
        sec_inds = [None for sec in self.secondaries]
        n = self.primary.map.Ny
        for i, sec in enumerate(self.secondaries):
            sec_inds[i] = np.arange(n, n + sec.map.Ny, dtype=int)
            n += sec.map.Ny

        # Compute the integral of the velocity-weighted intensity
        Iv = tt.as_tensor_variable(
            [tt.dot(X[:, pri_inds], pri_y)]
            + [
                tt.dot(X[:, sec_inds[n]], sec_y[n])
                for n in range(len(self.secondaries))
            ]
        )

        # Compute the inverse of the integral of the intensity
        invI = tt.as_tensor_variable(
            [tt.ones((1,)) / tt.dot(X0[:, pri_inds], pri_y)]
            + [
                tt.ones((1,)) / tt.dot(X0[:, sec_inds[n]], sec_y[n])
                for n in range(len(self.secondaries))
            ]
        )
        invI = tt.where(tt.isinf(invI), 0.0, invI)

        # The RV anomaly is just the product
        rv = tt.sum(Iv * invI, axis=0)

        # Compute the Keplerian RV
        orbit = exoplanet.orbits.KeplerianOrbit(
            period=sec_porb,
            t0=sec_t0,
            incl=sec_iorb,
            ecc=sec_ecc,
            omega=sec_w,
            Omega=sec_Omega,
            m_planet=sec_m,
            m_star=pri_m,
            r_star=pri_r,
        )
        return ifelse(
            keplerian,
            rv
            + tt.sum(
                orbit.get_radial_velocity(t, output_units=units.m / units.s),
                axis=-1,
            ),
            rv,
        )

    @autocompile
    def render(
        self,
        t,
        res,
        pri_r,
        pri_m,
        pri_prot,
        pri_t0,
        pri_theta0,
        pri_L,
        pri_inc,
        pri_obl,
        pri_y,
        pri_u,
        pri_f,
        pri_alpha,
        sec_r,
        sec_m,
        sec_prot,
        sec_t0,
        sec_theta0,
        sec_porb,
        sec_ecc,
        sec_w,
        sec_Omega,
        sec_iorb,
        sec_L,
        sec_inc,
        sec_obl,
        sec_y,
        sec_u,
        sec_f,
        sec_alpha,
    ):
        """Render all of the bodies in the system."""
        # Compute the relative positions of all bodies
        orbit = exoplanet.orbits.KeplerianOrbit(
            period=sec_porb,
            t0=sec_t0,
            incl=sec_iorb,
            ecc=sec_ecc,
            omega=sec_w,
            Omega=sec_Omega,
            m_planet=sec_m,
            m_star=pri_m,
            r_star=pri_r,
        )
        try:
            x, y, z = orbit.get_relative_position(
                t, light_delay=self.light_delay
            )
        except TypeError:
            if self.light_delay:
                logger.warn(
                    "This version of `exoplanet` does not model light delays."
                )
            x, y, z = orbit.get_relative_position(t)

        # Get all rotational phases
        pri_prot = ifelse(
            tt.eq(pri_prot, 0.0), math.to_tensor(np.inf), pri_prot
        )
        theta_pri = (2 * np.pi) / pri_prot * (t - pri_t0) + pri_theta0
        sec_prot = tt.switch(
            tt.eq(sec_prot, 0.0), math.to_tensor(np.inf), sec_prot
        )
        theta_sec = (2 * np.pi) / tt.shape_padright(sec_prot) * (
            tt.shape_padleft(t) - tt.shape_padright(sec_t0)
        ) + tt.shape_padright(sec_theta0)

        # Compute all the maps
        img_pri = self.primary.map.ops.render(
            res,
            STARRY_ORTHOGRAPHIC_PROJECTION,
            theta_pri,
            pri_inc,
            pri_obl,
            pri_y,
            pri_u,
            pri_f,
            pri_alpha,
        )
        if self._reflected:
            img_sec = tt.as_tensor_variable(
                [
                    sec.map.ops.render(
                        res,
                        STARRY_ORTHOGRAPHIC_PROJECTION,
                        theta_sec[i],
                        sec_inc[i],
                        sec_obl[i],
                        sec_y[i],
                        sec_u[i],
                        sec_f[i],
                        sec_alpha[i],
                        -x[:, i],
                        -y[:, i],
                        -z[:, i],
                    )
                    for i, sec in enumerate(self.secondaries)
                ]
            )
        else:
            img_sec = tt.as_tensor_variable(
                [
                    sec.map.ops.render(
                        res,
                        STARRY_ORTHOGRAPHIC_PROJECTION,
                        theta_sec[i],
                        sec_inc[i],
                        sec_obl[i],
                        sec_y[i],
                        sec_u[i],
                        sec_f[i],
                        sec_alpha[i],
                    )
                    for i, sec in enumerate(self.secondaries)
                ]
            )

        # Return the images and secondary orbital positions
        return img_pri, img_sec, x, y, z
