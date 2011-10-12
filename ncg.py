"""
Experimental non-linear conjugate gradient.
"""

__authors__ = "Olivier Delalleau, Razvan Pascanu"
__copyright__ = "(c) 2011, Universite de Montreal"
__license__ = "BSD"
__contact__ = "Olivier Delalleau <delallea@iro>"


from itertools import izip

import numpy
from scipy.optimize.optimize import (
        _epsilon, line_search_wolfe1, vecnorm, wrap_function)

import theano
import theano.tensor as TT
from theano.lazycond import ifelse
from theano.scan_module import until

from pylearn2.optimization.ncg import linesearch_module as linesearch
from pylearn2.optimization.ncg.ncg_module import (
        lazy_or, zero)


def leon_ncg_theano(cost_fn, x0s, args=(), gtol=1e-5,
        maxiter=None, profile=False):
    """
    Minimize a function using a nonlinear conjugate gradient algorithm.

    Parameters
    ----------
    cost_fn : callable f(*(xs+args))
        Objective function to be minimized.
    x0s : list of theano tensors
        Initial guess.
    args : tuple
        Extra arguments passed to cost_fn.
    gtol : float
        Stop when norm of gradient is less than gtol.
    maxiter : int
        Maximum number of iterations allowed for CG
    profile: flag (boolean)
        If profiling information should be printed

    Returns
    -------
    fopt : float
        Minimum value found, f(xopt).
    xopt : ndarray
        Parameters which minimize f, i.e. f(xopt) == fopt.

    Notes
    -----
    Optimize the function, f, whose gradient is given by fprime
    using the nonlinear conjugate gradient algorithm of Polak and
    Ribiere. See Wright & Nocedal, 'Numerical Optimization',
    1999, pg. 120-122.

    This function mimics `fmin_cg` from `scipy.optimize`.
    """
    if type(x0s) not in (tuple, list):
        x0s = [x0s]
    else:
        x0s = list(x0s)
    if type(args) not in (tuple, list):
        args = [args]
    else:
        args = list(args)


    if maxiter is None:
        len_x0 = sum(x0.size for x0 in x0s)
        maxiter = len_x0 * 200

    out = cost_fn(*(x0s+args))
    global_x0s = [x for x in x0s]
    def f(*nw_x0s):
        rval = theano.clone(out, replace=dict(zip(global_x0s, nw_x0s)))
        #rval = cost_fn(*nw_x0s)
        return rval


    def myfprime(*nw_x0s):
        gx0s = TT.grad(out, global_x0s, keep_wrt_type=True)
        rval = theano.clone(gx0s, replace=dict(zip(global_x0s, nw_x0s)))
        #import ipdb; ipdb.set_trace()
        #rval = TT.grad(cost_fn(*nw_x0s), nw_x0s)
        return [x for x in rval]


    n_elems = len(x0s)

    def fmin_cg_loop(old_fval, old_old_fval, *rest):
        xks  = rest[:n_elems]
        gfks = rest[n_elems:n_elems * 2]

        maxs = [ abs(gfk).max(axis=range(gfk.ndim)) for gfk in gfks ]
        if len(maxs) == 1:
            gnorm = maxs[0]
        else:
            gnorm = TT.maximum(maxs[0], maxs[1])
            for dx in maxs[2:]:
                gnorm = TT.maximum(gnorm, dx)

        pks  = rest[n_elems*2:]
        #import ipdb; ipdb.set_trace()
        deltak = sum((gfk * gfk).sum() for gfk in gfks)

        old_fval_backup = old_fval
        old_old_fval_backup = old_old_fval

        alpha_k, old_fval, old_old_fval, derphi0, nw_gfks = \
                linesearch.line_search_wolfe2(f,myfprime, xks, pks,
                                              old_fval_backup,
                                              old_old_fval_backup,
                                              profile = profile,
                                             gfks = gfks)



        xks = [ ifelse(gnorm <= gtol, xk,
                              ifelse(TT.bitwise_or(TT.isnan(alpha_k),
                                                          TT.eq(alpha_k,
                                                                zero)), xk,
                                            xk+alpha_k*pk)) for xk, pk in zip(xks,pks)]
        gfkp1s_tmp = myfprime(*xks)
        gfkp1s = [ ifelse(TT.isnan(derphi0), nw_x, x) for nw_x, x in
                  zip(gfkp1s_tmp, nw_gfks)]


        yks = [gfkp1 - gfk for gfkp1, gfk in izip(gfkp1s, gfks)]
        # Polak-Ribiere formula.
        beta_k = TT.maximum(
                zero,
                sum((x * y).sum() for x, y in izip(yks, gfkp1s)) / deltak)
        pks  = [ ifelse(gnorm <= gtol, pk,
                               ifelse(TT.bitwise_or(TT.isnan(alpha_k),
                                                           TT.eq(alpha_k,
                                                                 zero)), pk, -gfkp1 +
                                             beta_k * pk)) for gfkp1,pk in zip(gfkp1s,pks) ]
        gfks = [ifelse(gnorm <= gtol,
                       gfk,
                       ifelse(
                           TT.bitwise_or(TT.isnan(alpha_k),
                                         TT.eq(alpha_k, zero)),
                           gfk,
                           gfkp1))
                for (gfk, gfkp1) in izip(gfks, gfkp1s)]

        stop = lazy_or(gnorm <= gtol, TT.bitwise_or(TT.isnan(alpha_k),
                                                TT.eq(alpha_k, zero)))# warnflag = 2
        old_fval     = ifelse(gnorm >gtol, old_fval, old_fval_backup)
        old_old_fval = ifelse(gnorm >gtol, old_old_fval,
                                     old_old_fval_backup)
        return ([old_fval, old_old_fval]+xks + gfks + pks,
                until(stop))

    gfks = myfprime(*x0s)
    xks = x0s
    old_fval = f(*xks)

    old_old_fval = old_fval + numpy.asarray(5000, dtype=theano.config.floatX)

    old_fval.name = 'old_fval'
    old_old_fval.name = 'old_old_fval'
    pks = [-gfk for gfk in gfks]

    outs, _ = theano.scan(fmin_cg_loop,
                          outputs_info = [old_fval,
                                          old_old_fval] + xks + gfks + pks,
                          n_steps = maxiter,
                          name = 'fmin_cg',
                          mode = theano.Mode(linker='cvm_nogc'),
                          profile = profile)

    sol = [outs[0][-1]] + [x[-1] for x in outs[2:2+n_elems]]
    return sol

def leon_ncg_python(f, x0, fprime=None, args=(), gtol=1e-5, norm=numpy.Inf, epsilon=_epsilon,
              maxiter=None, full_output=0, disp=1, retall=0, callback=None,
              direction='hestenes-stiefel',
              ):
    """Minimize a function using a nonlinear conjugate gradient algorithm.

    Parameters
    ----------
    f : callable f(x,*args)
    Objective function to be minimized.
    x0 : ndarray
    Initial guess.
    fprime : callable f'(x,*args)
    Function which computes the gradient of f.
    args : tuple
    Extra arguments passed to f and fprime.
    gtol : float
    Stop when norm of gradient is less than gtol.
    norm : float
    Order of vector norm to use. -Inf is min, Inf is max.
    epsilon : float or ndarray
    If fprime is approximated, use this value for the step
    size (can be scalar or vector).
    callback : callable
    An optional user-supplied function, called after each
    iteration. Called as callback(xk, beta_k), where xk is the
    current parameter vector and beta_k the coefficient for the
    new direction.
    direction : string
    Formula used to computed the new direction, among:
        - polak-ribiere
        - hestenes-stiefel

    Returns
    -------
    xopt : ndarray
    Parameters which minimize f, i.e. f(xopt) == fopt.
    fopt : float
    Minimum value found, f(xopt).
    func_calls : int
    The number of function_calls made.
    grad_calls : int
    The number of gradient calls made.
    warnflag : int
    1 : Maximum number of iterations exceeded.
    2 : Gradient and/or function calls not changing.
    allvecs : ndarray
    If retall is True (see other parameters below), then this
    vector containing the result at each iteration is returned.

    Other Parameters
    ----------------
    maxiter : int
    Maximum number of iterations to perform.
    full_output : bool
    If True then return fopt, func_calls, grad_calls, and
    warnflag in addition to xopt.
    disp : bool
    Print convergence message if True.
    retall : bool
    Return a list of results at each iteration if True.

    Notes
    -----
    Optimize the function, f, whose gradient is given by fprime
    using the nonlinear conjugate gradient algorithm of Polak and
    Ribiere. See Wright & Nocedal, 'Numerical Optimization',
    1999, pg. 120-122.
    """
    x0 = numpy.asarray(x0).flatten()
    if maxiter is None:
        maxiter = len(x0)*200
    func_calls, f = wrap_function(f, args)
    if fprime is None:
        grad_calls, myfprime = wrap_function(approx_fprime, (f, epsilon))
    else:
        grad_calls, myfprime = wrap_function(fprime, args)
    gfk = myfprime(x0)
    k = 0
    N = len(x0)
    xk = x0
    old_fval = f(xk)
    old_old_fval = old_fval + 5000

    if retall:
        allvecs = [xk]
    sk = [2*gtol]
    warnflag = 0
    pk = -gfk
    gnorm = vecnorm(gfk, ord=norm)

    # gfk    <->  f'(x_k)
    # gfkp1  <->  f'(x_{k+1})
    # deltak <->  || f'(x_k) ||^2
    # yk     <->  f'(x_{k+1}) - f'(x_k)
    # pk     <->  d_k
    while (gnorm > gtol) and (k < maxiter):
        deltak = numpy.dot(gfk, gfk)

        # These values are modified by the line search, even if it fails
        old_fval_backup = old_fval
        old_old_fval_backup = old_old_fval

        alpha_k, fc, gc, old_fval, old_old_fval, gfkp1 = \
                 line_search_wolfe1(f, myfprime, xk, pk, gfk, old_fval,
                                  old_old_fval, c2=0.4)
        if alpha_k is None: # line search failed -- use different one.
            alpha_k, fc, gc, old_fval, old_old_fval, gfkp1 = \
                     line_search_wolfe2(f, myfprime, xk, pk, gfk,
                                        old_fval_backup, old_old_fval_backup)
            if alpha_k is None or alpha_k == 0:
                # This line search also failed to find a better solution.
                warnflag = 2
                break
        xk = xk + alpha_k * pk
        if retall:
            allvecs.append(xk)
        if gfkp1 is None:
            gfkp1 = myfprime(xk)
        yk = gfkp1 - gfk
        if direction == 'polak-ribiere':
            # Polak-Ribiere.
            beta_k = max(0, numpy.dot(yk, gfkp1) / deltak)
        elif direction == 'hestenes-stiefel':
            # Hestenes-Stiefel.
            beta_k = max(0, numpy.dot(yk, gfkp1) / numpy.dot(yk, pk))
        else:
            raise NotImplementedError(direction)
        pk = -gfkp1 + beta_k * pk
        gfk = gfkp1
        gnorm = vecnorm(gfk, ord=norm)
        if callback is not None:
            callback(xk, beta_k)
        k += 1


    if disp or full_output:
        fval = old_fval
    if warnflag == 2:
        if disp:
            print "Warning: Desired error not necessarily achieved due to precision loss"
            print " Current function value: %f" % fval
            print " Iterations: %d" % k
            print " Function evaluations: %d" % func_calls[0]
            print " Gradient evaluations: %d" % grad_calls[0]

    elif k >= maxiter:
        warnflag = 1
        if disp:
            print "Warning: Maximum number of iterations has been exceeded"
            print " Current function value: %f" % fval
            print " Iterations: %d" % k
            print " Function evaluations: %d" % func_calls[0]
            print " Gradient evaluations: %d" % grad_calls[0]
    else:
        if disp:
            print "Optimization terminated successfully."
            print " Current function value: %f" % fval
            print " Iterations: %d" % k
            print " Function evaluations: %d" % func_calls[0]
            print " Gradient evaluations: %d" % grad_calls[0]


    if full_output:
        retlist = xk, fval, func_calls[0], grad_calls[0], warnflag
        if retall:
            retlist += (allvecs,)
    else:
        retlist = xk
        if retall:
            retlist = (xk, allvecs)

    return retlist



