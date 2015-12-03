from __future__ import division
import numpy as np
import multiprocessing as mp
import math
from scipy import optimize
import os
import warnings
import scipy
import time
import forward_diff

'''
IMPORTANT: this code is based basically based on two references.
    ref I: Solving Ordinary Differential Equations I: Nonstiff Problems by Hairer, Norsett and Wanner
    ref II: Solving Ordinary Differntial Equations II: Stiff and Differential-Algebraic Problems by Hairer and Wanner
'''

warnings.filterwarnings('ignore')

#TODO: remove use grad (if passed use always)
useGrad = False

NUM_WORKERS = None


def set_NUM_WORKERS(nworkers):
    '''
    Set number of parallel workers to be used to parallelize solver's operations
    (if None it is set to the number of processors of computer). 
    
    @param nworkers (int): number of parallel workers
    '''
    global NUM_WORKERS
    if nworkers == None:
        try:
            NUM_WORKERS = mp.cpu_count()
        except NotImplementedError:
            NUM_WORKERS = 4
    else: 
        NUM_WORKERS = max(nworkers, 1)

def error_norm(y1, y2, atol, rtol):
    '''
    Error norm/measure between y1, y2.
    Based on II.4.11 (ref I).
    
    @param y1 (array): first value
    @param y2 (array): second value
    @param atol (float): absolute tolerance required
    @param rtol (float): relative tolerance required
    '''
    tol = atol + np.maximum(np.abs(y1),np.abs(y2))*rtol
    return np.linalg.norm((y1-y2)/tol)/(len(y1)**0.5)

'''''
BEGINNING: ODE numerical methods formulas and one step solvers(explicit, implicit and semi-implicit).

Obs: semi-implicit is also called in the reference books as linearly implicit.

Methods' common structure:
def method_implicit/explicit/semi-implicit(f, grad, previousValues, previousTime,f_previousValue, step, args)
    @param f (callable f(y,t,args)): derivative of u(t) (ODE RHS, f(u,t))
    @param grad (callable grad(y,t,args)): Jacobian of f.
    @param previousValues (2-tuple) : previous solution values (two previous values, at t_n-2 and t_n-1) 
            used to obtain the next point (using two previous values because midpoint explicit
            method needs the previous two points, otherwise the t_n-2 value can remain unused)
    @param previousTime (float): time at which previous solution is found
    @param f_previousValue (array): function evaluation at the previousValue (at t_n-1), so that it can be
            reused if it was already calculated for other purposes (Jacobian estimation or dense output interpolation)
    @param step (float): step length
    @param args (tuple): extra arguments
    @param extra arguments to be passed to some of the methods:
        @param J00 (2D array): (only for semi-implicit) Jacobian estimation at the previous stage value
                (at each extrapolation stage different number of steps are taken, depending on the
                step sequence chosen).
        
    @return (yj, f_yj, fe_tot, je_tot):
        @return yj (array): solution calculated at previousTime+step
        @return f_yj (array): function evaluation value at previousValue, previousTime
        @return fe_tot (int): number of function evaluations done in this method
        @return je_tot (int): number of Jacobian evaluations done in this method
'''''

def solve_implicit_step(zero_f, zero_grad, estimatedValueExplicit):
    '''
    Find the root of the zero_f function using as initial approximation estimatedValueExplicit.
    This zero_f root is the next step solution.
    
    @param zero_f (callable zero_f(y,t,args)): function to find the root of (estimation of next step)
    @param zero_grad (callable zero_grad(y,t,args)): Jacobian of zero_f.
    @param estimatedValueExplicit (array): estimated value of the zero_f function to use as initial value
            for the root solver
    
    @return (yj, fe_tot, je_tot):
        @return yj (array): solution calculated at previousTime+step (root of the zero_f)
        @return fe_tot (int): number of function evaluations done in this method
        @return je_tot (int): number of Jacobian evaluations done in this method 
    '''
    #TODO: Problem -> fsolve doesn't seem to work well with xtol parameter
    #TODO: pass to the zero finder solver the tolerance required to the overall problem 
    # by doing this the error by the solver doesn't limit the global error of the ODE's solution
    
    global useGrad
    if(not useGrad):
        zero_grad=None
    
    x, infodict, ier, mesg = optimize.fsolve(zero_f,estimatedValueExplicit, fprime = zero_grad, full_output = True,xtol=1e-15)
#     print ("jac ev: " + str(infodict["njev"]))
#     print ("func ev: " + str(infodict["nfev"]))
    #TODO change solver so it doesn't do the 2 extra unnnecessary function evaluations
    #https://github.com/scipy/scipy/issues/5369
    #TODO: add extra 2 function evaluations
    if("njev" in infodict):
        return (x, infodict["nfev"], infodict["njev"])
    else:
        return (x, infodict["nfev"], 0)

#     optObject = optimize.root(zero_f, estimatedValueExplicit, jac = zero_grad)
# #     if(useGrad and not zero_grad is None):
# #         print ("jac ev: " + str(optObject.njev))
# #     print ("func ev: " + str(optObject.nfev))
#     return (optObject.x, optObject.nfev)


def midpoint_semiimplicit(f, grad, previousValues, previousTime,f_previousValue, step, args, J00):
    """
    Creates midpoint function method formula u_n+1=u_n+h*f(
    
    !!!IMPORTANT TODO: Use the same framework as euler_semiimplicit when it comes to solving the system (iteratively or exactly)
    and whether the jacobian is sparse or not. Do not copy code, share it for both midpoint and euler!

    
    """
    
    previousPreviousValue, previousValue = previousValues
       
    if(previousPreviousValue is None):
        return euler_semiimplicit(f, grad, previousValues, previousTime,f_previousValue, step, args, J00)
    
    if(f_previousValue is None):
        f_yj = f(*(previousValue,previousTime)+args)
        fe_tot = 1
    else:
        f_yj = f_previousValue
        fe_tot=0
        
    x = previousValue + np.linalg.solve(I-step*J00,np.dot(-(I+step*J00),(previousValue-previousPreviousValue)) + 2*step*f_yj)
    
    return (x, f_yj, fe_tot, 0)

def isSparseMatrix(J):
    return (isinstance(J, scipy.sparse.csr_matrix) or isinstance(J, scipy.sparse.coo_matrix)
            or isinstance(J, scipy.sparse.csc_matrix) or isinstance(J, scipy.sparse.dia_matrix))

min_tol = 1e-5
useIterative=True
def setiterative(iterative):
    global useIterative
    useIterative= iterative
    
addinitialguess=True

def setaddinitialguess(initialguess):
    global addinitialguess
    addinitialguess=initialguess

I=0
Isparse=0

def initializeIdentity(N):
    '''
    
    '''
    global Isparse, I
    Isparse = scipy.sparse.identity(N, dtype = float, format = 'csr')
    I = np.identity(N, dtype=float)


def euler_semiimplicit(f, grad, previousValues, previousTime, f_previousValue,step, args, J00):
    """
    Creates midpoint function method formula u_n+1=u_n+h*f(
    
    @param f: derivative of u(t) (ODE RHS, f(u,t))
    @param previousValue: previous solution value used to obtain of next point
    @param previousTime: time at which previous solution is found
    @param step: step length
    
    @return implicit method's solution
    """
    
    previousPreviousValue, previousValue = previousValues
    je_tot=0
    if(f_previousValue is None):
        f_yj = f(*(previousValue,previousTime)+args)
        fe_tot = 1
    else:
        f_yj = f_previousValue
        fe_tot=0
        
    if(not addinitialguess):
        xval=None
    else:
        # The option of doing an explicit step doesn't seem to be effective (maybe because with
        # extrapolation we are taking too big steps for the explicit solver to be close to the solution).
        # Doing the euler_explicit doesn't cost extra function evaluations
        # This idea came from the lsode solver.
#         xval, f_yj, fe_tot_,je_tot=euler_explicit(f, grad, previousValues, previousTime, f_yj, step, args)
#         fe_tot += fe_tot_
        xval = previousValue
    
    #TODO: change this checking of the sparsity type of the matrix only once
    # at the beginning of the ODE solving 
    #Choose system solver: 
    #http://scicomp.stackexchange.com/questions/3262/how-to-choose-a-method-for-solving-linear-equations
    if(isSparseMatrix(J00)):
        if(useIterative):
            #The algorithm terminates when either the relative or the absolute residual is below tol.
            #Therefore we use the minimum tolerance (more restrictive value)
            sol, info= scipy.sparse.linalg.gmres(Isparse-step*J00, step*f_yj, tol=min_tol,x0=xval, maxiter=100)                             
            if info >0:
                print("Info: maximum iterations reached for sparse system solver (GMRES).")
        else:
            sol = scipy.sparse.linalg.spsolve(Isparse-step*J00, step*f_yj)
            
        x = previousValue + sol
    else:
        #TODO: choose either exact solver or iterative solver for dense non-analytical jacobian
        #Iterative is better for larger systems.
        if(not useIterative):
            sol = np.linalg.solve(I-step*J00, step*f_yj)
        else:
            sol, info= scipy.sparse.linalg.gmres(I-step*J00, step*f_yj, tol=min_tol,x0=xval, maxiter=100)                             
        x = previousValue + sol
    return (x, f_yj, fe_tot, je_tot)


def midpoint_implicit(f, grad, previousValues, previousTime,f_previousValue, step, args):
    """
    Creates midpoint function method formula u_n+1=u_n+h*f(
    
    @param f: derivative of u(t) (ODE RHS, f(u,t))
    @param previousValue: previous solution value used to obtain of next point
    @param previousTime: time at which previous solution is found
    @param step: step length
    
    @return implicit method's solution
    """
    previousPreviousValue, previousValue = previousValues
    
    def zero_func(x):
        return x-previousValue-step*f(*((previousValue+x)/2, previousTime+step/2) + args)
    
    def zero_grad(x):
        return np.matrix(np.identity(len(x), dtype=float) - step*grad(x,previousTime+step/2))

    #Estimation of the value as the starting point for the zero solver 
    estimatedValueExplicit, f_yj, fe_tot, je_tot=euler_explicit(f, grad, previousValues, previousTime,f_previousValue, step, args)
    
    if(grad is None):
        zero_grad=None
        
    x, fe_tot_, je_tot_ = solve_implicit_step(zero_func, zero_grad, estimatedValueExplicit)
    fe_tot +=fe_tot_
    je_tot += je_tot_
    
    #TODO: see it this extra function can be done only if interpolating polynomial is calculated (looks complicated)
    #This can't follow the midpoint_explicit approach because in midpoint_implicit we are doing function evaluations
    #at previousTime+step/2
    f_yj= f(*(previousValue,previousTime)+args)
    fe_tot += 1
    return (x, f_yj, fe_tot, je_tot)


def midpoint_explicit(f, grad, previousValues, previousTime,f_previousValue, step, args):
    """
    Creates midpoint function method formula u_n+1=u_n+h*f(
    
    @param f: derivative of u(t) (ODE RHS, f(u,t))
    @param previousValues: previous solution values used to obtain of next point
    @param previousTime: time at which previous solution is found
    @param step: step length
    
    @return estimated value at previousTime + step, function evaluation and number of function evaluations
    """
    previousPreviousValue, previousValue = previousValues
    if(previousPreviousValue is None):
        return euler_explicit(f, grad, previousValues, previousTime, f_previousValue,step, args)
    
    f_yj = f(*(previousValue, previousTime)+args)
    fe_tot=1
    return (previousPreviousValue + (2*step)*f_yj, f_yj, fe_tot,0)

def euler_explicit(f, grad, previousValues, previousTime,f_previousValue, step, args):
    previousPreviousValue, previousValue = previousValues
    
    if(f_previousValue is None):
        f_yj = f(*(previousValue,previousTime)+args)
        fe_tot = 1
    else:
        f_yj = f_previousValue
        fe_tot=0
    
    return (previousValue + step*f_yj, f_yj, fe_tot,0)


'''''
END: ODE numerical methods formulas (explicit, implicit and semi-implicit)
'''''

def compute_stages((method, methodargs, func, grad, tn, yn, f_yn, args, h, k_nj_lst, smoothing)):
    res = []
    for (k,nj) in k_nj_lst:
        fe_tot=0
        je_tot=0
        nj = int(nj)
        Y = np.zeros((nj+1, len(yn)), dtype=(type(yn[0])))
        f_yj = np.zeros((nj+1, len(yn)), dtype=(type(yn[0])))
        Y[0] = yn
        step = h/nj

        Y[1], f_yj[0], fe_tot_, je_tot_ = method(func, grad, (None, Y[0]), tn, f_yn, step, args, **methodargs)
        fe_tot += fe_tot_
        je_tot += je_tot_
        for j in range(2,nj+1):
            Y[j], f_yj[j-1], fe_tot_ , je_tot_= method(func, grad, (Y[j-2], Y[j-1]), tn + (j-1)*(h/nj), None, step, args, **methodargs)
            fe_tot += fe_tot_
            je_tot += je_tot_
        y_half = Y[nj/2]
        #Perform smoothing step
        Tj1 = Y[nj]
        if(not smoothing == 'no'):
            nextStepSolution, f_yj_unused, fe_tot_, je_tot_ = method(func, grad, (Y[nj-1], Y[nj]), tn + h, None, step, args, **methodargs)
            fe_tot += fe_tot_
            je_tot += je_tot_
            if(smoothing == 'gbs'):
                Tj1 = 1/4*(Y[nj-1]+2*Y[nj]+nextStepSolution)
            elif(smoothing == 'semiimp'):
                Tj1 = 1/2*(Y[nj-1]+nextStepSolution)
        res += [(k, nj, Tj1, y_half, f_yj,Y, fe_tot, je_tot)]

    return res

def extrapolation_parallel (method, methodargs, func, grad, y0, t, args=(), full_output=False,
        rtol=1.0e-8, atol=1.0e-8, h0=0.5, mxstep=10e4, robustness_factor=2, p=4,
        nworkers=None, smoothing='no', symmetric=True, seq=None, adaptative="order"):   
    '''
    Solves the system of IVPs dy/dt = func(y, t0, ...) with parallel extrapolation. 
    
    **Parameters**
        - method: callable()
            The method on which the extrapolation is based
        - func: callable(y, t0, ...)
            Computes the derivative of y at t0 (i.e. the right hand side of the 
            IVP). Must output a non-scalar numpy.ndarray
        - y0 : numpy.ndarray
            Initial condition on y (can be a vector). Must be a non-scalar 
            numpy.ndarray
        - t : array
            A sequence of time points for which to solve for y. The initial 
            value point should be the first element of this sequence. And the last 
            one the final time
        - args : tuple, optional
            Extra arguments to pass to function.
        - full_output : bool, optional
            True if to return a dictionary of optional outputs as the second 
            output. Defaults to False

    **Returns**
        - ys : numpy.ndarray, shape (len(t), len(y0))
            Array containing the value of y for each desired time in t, with 
            the initial value y0 in the first row.
        - infodict : dict, only returned if full_output == True
            Dictionary containing additional output information
            KEY         MEANING
            'fe_seq'    cumulative number of sequential derivative evaluations
            'fe_tot'    cumulative number of total derivative evaluations
            'nstp'      cumulative number of successful time steps
            'h_avg'     average step size if adaptive == "order" (None otherwise)
            'k_avg'     average extrapolation order if adaptive == "order" 
                        ... (None otherwise)

    **Other Parameters**
        - rtol, atol : float, optional
            The input parameters rtol and atol determine the error control 
            performed by the solver. The solver will control the vector, 
            e = y2 - y1, of estimated local errors in y, according to an 
            inequality of the form l2-norm of (e / (ewt * len(e))) <= 1, 
            where ewt is a vector of positive error weights computed as 
            ewt = atol + max(y1, y2) * rtol. rtol and atol can be either vectors
            the same length as y0 or scalars. Both default to 1.0e-8.
        - h0 : float, optional
            The step size to be attempted on the first step. Defaults to 0.5
        - mxstep : int, optional
            Maximum number of (internally defined) steps allowed for each
            integration point in t. Defaults to 10e4
        - adaptive: string, optional
            Specifies the strategy of integration. Can take three values:
            -- "fixed" = use fixed step size and order strategy.
            -- "step"  = use adaptive step size but fixed order strategy.
            -- "order" = use adaptive step size and adaptive order strategy.
            Defaults to "order"
        - p: int, optional
            The order of extrapolation if adaptive is not "order", and the 
            starting order otherwise. Defaults to 4
        - seq: callable(k) (k: positive int), optional
            The step-number sequence. Defaults to the harmonic sequence given 
            by (lambda t: 2*t)
        - nworkers: int, optional
            The number of workers working in parallel. If nworkers==None, then 
            the the number of workers is set to the number of CPUs on the the
            running machine. Defaults to None.
    '''
    global min_tol
    min_tol = min(atol,rtol)
    
    #Initialize pool of workers to parallelize extrapolation table calculations
    set_NUM_WORKERS(nworkers)  
    pool = mp.Pool(NUM_WORKERS)

    #Initialize identity matrices with problem's size (needed for semiimplicit methods)
    initializeIdentity(len(y0))
    
    assert len(t) > 1, ("the array t must be of length at least 2, " + 
    "the initial value time should be the first element of t and the last " +
    "element of t the final time")

    # ys contains the solutions at the times specified by t
    ys = np.zeros((len(t), len(y0)), dtype=(type(y0[0])))
    ys[0] = y0
    t0 = t[0]

    #Initialize values
    fe_seq = 0
    fe_tot = 0
    je_tot = 0
    nstp = 0
    cur_stp = 0

    t_max = t[-1]
    t_index = 1

    #yn is the previous calculated step (at t_curr)
    yn, t_curr, k = 1*y0, t0, p
    h = min(h0, t_max-t0)

    sum_ks, sum_hs = 0, 0
    
    #Initialize rejectStep so that Jacobian is updated
    rejectStep = True
    previousStepSolution=()

    #Iterate until you reach final time
    while t_curr < t_max:
        rejectStep, y_temp, ysolution,f_yn, h, k, h_new, k_new, (fe_seq_, fe_tot_, je_tot_) = solve_one_step(
                method, methodargs, func, grad, t_curr, t, t_index, yn, args, h, k, 
                atol, rtol, pool, smoothing, symmetric, seq, adaptative, rejectStep, previousStepSolution)
        #previousStepSolution is used for Jacobian updating
        previousStepSolution=(yn,f_yn)

        #Store values if step is not rejected
        if(not rejectStep):
            yn = 1*y_temp
            #ysolution includes all intermediate solutions in interval
            if(len(ysolution)!=0):
                ys[t_index:(t_index+len(ysolution))] = ysolution
            
            #Update time
            t_curr += h
            t_index += len(ysolution)
            #add last solution if matches an asked time (in t)
            if(t[t_index]==t_curr):
                ys[t_index] = yn
                t_index+=1

        #Update function evaluations
        fe_seq += fe_seq_
        fe_tot += fe_tot_
        je_tot += je_tot_

        sum_ks += k
        sum_hs += h
        nstp += 1
        cur_stp += 1

        if cur_stp > mxstep:
            raise Exception('Reached Max Number of Steps. Current t = ' 
                + str(t_curr))
        
        #Sometimes step can be NaN due to overflows of the RHS 
        if(math.isnan(h_new)):
            h_new = h/robustness_factor
        #Update to new step (limit the change of h_new by a robustness_factor)
        elif(h_new>h and h_new/h>robustness_factor):
            h_new = h*robustness_factor
        elif(h_new<h and h_new/h<1/robustness_factor):
            h_new = h/robustness_factor
        #Check that h_new doesn't step after t_max
        h = min(h_new, t_max - t_curr)
        #Keeps the code from taking a close to machine precision step
        if (adaptative=="fixed" and (t_max-(t_curr+h))/t_max<1e-12):
            h=t_max-t_curr
            
        k = k_new

    #Close pool of workers and return results
    pool.close()

    if full_output:
        infodict = {'fe_seq': fe_seq, 'nfe': fe_tot, 'nst': nstp, 'nje': je_tot,
                    'h_avg': sum_hs/nstp, 'k_avg': sum_ks/nstp}
        return (ys, infodict)
    else:
        return ys

def balance_load(k, seq=(lambda t: 2*t)):
#     return [(i,seq(i)) for i in range(k, 0, -1)]
    if k <= NUM_WORKERS:
        k_nj_lst = [[(i,seq(i))] for i in range(k, 0, -1)]
    else:
        k_nj_lst = [[] for i in range(NUM_WORKERS)]
        index = range(NUM_WORKERS)
        i = k
        while 1:
            if i >= NUM_WORKERS:
                for j in index:
                    k_nj_lst[j] += [(i, seq(i))]
                    i -= 1
            else:
                for j in index:
                    if i == 0:
                        break
                    k_nj_lst[j] += [(i, seq(i))]
                    i -= 1
                break
            index = index[::-1]
     
    return k_nj_lst

allvaps = np.array([])

def setallvaps():
    global allvaps
    allvaps=np.array([])

def getallvaps():
    global allvaps
    return allvaps

def updateJ00(previousJ00,func, yn, tn, yn_1, f_yn_1, args):
    updatedJ00 = previousJ00
    return (updatedJ00,None)
    f_yn = func(*(yn,tn)+args)
    incf_yn = f_yn-f_yn_1
    incyn = yn-yn_1
    updatedJ00 = previousJ00 +np.outer((incf_yn-np.dot(previousJ00,incyn))/np.linalg.norm(incyn, 2),incyn)
    return (updatedJ00,f_yn)

def setfrezeejacobian(first):
    global freeze
    freeze = first
    
freeze = False
previousJ00 = 0
def getJacobian(func, args, yn, tn, grad, methodargs, rejectPreviousStep, previousStepSolution): 
    je_tot=0
    fe_tot=0
    f_yn=None
#     prof = cProfile.Profile()
    if(not methodargs=={}):
        #TODO: to this function evaluations add methodargs parameter!!
        def func_at_tn(y, args):
            return func(*(y,tn)+args)
        if(not useGrad or grad is None):
            #TODO: add to func_at_tn the extra arguments in methodargs
            #TODO: add information if the jacobian matrix is known to be banded (ml,mu)
            #Currently ml and mu is set so that all Jacobian matrix is estimated.
            
#             J = ndt.Jacobian(func_at_tn)
#             
#             J00=prof.runcall(J,yn)
#             methodargs['J00'] = J00
#             prof.create_stats()
#             stats = prof.stats
#             stats_function=[value for key,value in stats.items() if ('twelve' in key[0])]
#             fe_tot = stats_function[0][0]
            global previousJ00
            if(freeze and not rejectPreviousStep):
                #TODO: update J00 Broyden-style
                yn_1, f_yn_1 = previousStepSolution
                updatedJ00, f_yn = updateJ00(previousJ00,func, yn, tn, yn_1, f_yn_1, args)
                methodargs['J00']=updatedJ00
                return (f_yn, fe_tot,je_tot)
    
            f_yn = func_at_tn(yn,args)
            fe_tot += 1
            J00,fe_tot_ = forward_diff.Jacobian(func_at_tn,yn, f_yn, args)
            fe_tot += fe_tot_
            je_tot=1
            methodargs['J00'] =J00 
            previousJ00 = J00
        else:            
            J00 = grad(yn, tn)
            je_tot = 1
            fe_tot = 0
            #Code that calculates the eigenvalues of the linearization of the problem
#             vaps = np.linalg.eigvals(J00.todense())
#             global allvaps
#             allvaps = np.concatenate((allvaps,vaps))
            methodargs['J00'] =J00 
    return (f_yn, fe_tot,je_tot) 

def compute_extrapolation_table(method, methodargs, func, grad, tn, yn, args, h, k, pool, 
                            rejectPreviousStep,previousStepSolution, seq=(lambda t: 2*t), smoothing='no', symmetric = True):
    """
    **Inputs**:

        - func:         RHS of ODE
        - tn, yn:       time and solution values from previous step
        - args:         any extra args to func
        - h:            proposed step size
        - k:            proposed # of extrapolation iterations
        - pool:         parallel worker pool
        - seq:          extrapolation step number sequence
    """
    T = np.zeros((k+1,k+1, len(yn)), dtype=(type(yn[0])))
    k_nj_lst = balance_load(k, seq=seq)
    
    f_yn, fe_tot, je_tot = getJacobian(func, args, yn, tn, grad, methodargs, rejectPreviousStep, previousStepSolution)
    
    jobs = [(method, methodargs, func, grad, tn, yn, f_yn, args, h, k_nj, smoothing) for k_nj in k_nj_lst]

    results = pool.map(compute_stages, jobs, chunksize=1)
#     res = compute_stages((method, methodargs, func, grad, tn, yn, f_yn, args, h, k_nj_lst, smoothing))
    
    #At this stage fe_tot has only counted the function evaluations for the jacobian estimation
    fe_seq = 1*fe_tot
    fe_tot_stage_max=0
    # process the returned results from the pool 
    y_half = (k+1)*[None]
    f_yj = (k+1)*[None]
    yj = (k+1)*[None]
    hs = (k+1)*[None]
    
    for res in results:
#     for i in range(1,2):
        fe_tot_stage = 0
        for (k_, nj_, Tk_, y_half_, f_yj_, yj_, fe_tot_, je_tot_) in res:
            T[k_, 1] = Tk_
            y_half[k_] = y_half_
            f_yj[k_] = f_yj_
            yj[k_] = yj_
            hs[k_] = h/nj_
            fe_tot += fe_tot_
            je_tot += je_tot_
            fe_tot_stage += fe_tot_
        #Count the maximum number of sequential 
        #function evaluations taken
        if(fe_tot_stage_max<fe_tot_stage):
            fe_tot_stage_max = fe_tot_stage 
    
    fe_seq += fe_tot_stage_max
    fill_extrapolation_table(T, k, 0, seq, symmetric)
    
    return (T, fe_seq, fe_tot, je_tot, yn, y_half, f_yj, yj, f_yn, hs)

def fill_extrapolation_table(T, k, j_initshift, seq, symmetric):
    '''''
    Fill extrapolation table using the first column values 
    (calculated through parallel computation)
    
    @param T: extrapolation table (lower triangular) containing
    the first column calculated and to be filled
    @param k: table size
    @param seq: step sequence of the first column values
    @return: nothing
    
    '''''
    # compute extrapolation table 
    for i in range(2, k+1):
        for j in range(i, k+1):
            if(symmetric):
                T[j,i] = T[j,i-1] + (T[j,i-1] - T[j-1,i-1])/((seq(j+j_initshift)/seq(j+j_initshift-i+1))**2 - 1)
            else:
                T[j,i] = T[j,i-1] + (T[j,i-1] - T[j-1,i-1])/((seq(j+j_initshift)/seq(j+j_initshift-i+1)) - 1)        
            
def centered_finite_diff(j, f_yj, hj):
    # Called by interpolate_sym
    max_order = 2*j
    nj = len(f_yj) - 1
    coeff = [1,1]
    dj = (max_order+1)*[None]
    dj[1] = 1*f_yj[nj/2]
    dj[2] = (f_yj[nj/2+1] - f_yj[nj/2-1])/(2*hj)

    for order in range(2,max_order):
        coeff = [1] + [coeff[j] + coeff[j+1] for j in range(len(coeff)-1)] + [1]
        index = [nj/2 + order - 2*i for i in range(order+1)]
        sum_ = 0
        for i in range(order+1):
            sum_ += ((-1)**i)*coeff[i]*f_yj[index[i]]
        dj[order+1] = sum_ / (2*hj)**order 

    return dj

def backward_finite_diff(j, yj, hj,lam):
    # Called by interpolate_sym
    max_order = j-lam
    nj = len(yj) - 1
    coeff = [1,1]
    dj = (max_order+1)*[None]
    dj[1] = (yj[nj] - yj[nj-1])/hj
    
    for order in range(2,max_order+1):
        coeff = [1] + [coeff[j] + coeff[j+1] for j in range(len(coeff)-1)] + [1]
        index = [nj - i for i in range(order+1)]

        sum_ = 0
        for i in range(order+1):
            sum_ += ((-1)**i)*coeff[i]*yj[index[i]]
        dj[order] = sum_ /hj**order 

    return dj

def compute_rs(yj, hs, k, seq=(lambda t: 4*t-2)):
    # Called by interpolate_sym
    lam=0
    dj_kappa = np.zeros((k+1-lam, k+1), dtype=(type(yj[1][0])))
    rs = np.zeros((k+1), dtype=(type(yj[1][0])))
    
    for j in range(1+lam,k+1):
        dj_ = backward_finite_diff(j,yj[j], hs[j],lam)
        for kappa in range(1,j+1-lam):    
            dj_kappa[kappa,j] = 1*dj_[kappa]
    
    for kappa in range(1,k+1-lam):
        numextrap = k+1-kappa-lam
        T = np.zeros((numextrap+1, numextrap+1), dtype=(type(yj[1][0])))
        T[:,1] = 1*dj_kappa[kappa, (kappa+lam-1):]

        fill_extrapolation_table(T, numextrap, lam+kappa-1, seq, symmetric=False)

        rs[kappa] = 1*T[numextrap,numextrap] 

    return rs 

def compute_ds(y_half, f_yj, hs, k, seq=(lambda t: 4*t-2)):
    # Called by interpolate_sym
    dj_kappa = np.zeros((2*k+1, k+1), dtype=(type(y_half[1])))
    ds = np.zeros((2*k+1), dtype=(type(y_half[1])))
    
    for j in range(1,k+1):
        dj_kappa[0,j] = 1*y_half[j]
        dj_ = centered_finite_diff(j,f_yj[j], hs[j])
        for kappa in range(1,2*j+1):    
            dj_kappa[kappa,j] = 1*dj_[kappa]
    
    #skip value is used because extrapolation is required
    #(k-l) times for dj_(2l-1) and dj_(2l)
    skip = 0
    for kappa in range(2*k+1):
        numextrap = k-int(skip/2)
        T = np.zeros((numextrap+1, numextrap+1), dtype=(type(y_half[1])))
        T[:,1] = 1*dj_kappa[kappa, int(skip/2):]
        
        fill_extrapolation_table(T, numextrap, int(skip/2), seq, symmetric=True)
        
        ds[kappa] = 1*T[numextrap,numextrap] 
        if (kappa != 0):
            skip +=1

    return ds 

def getPolynomial(a_u,a_u_1,H,degree,pol_shift,atol,rtol):
    def poly (t):
        res = 1*a_u[0] 
        for i in range(1, len(a_u)):
            res += a_u[i]*((t-pol_shift)**i)
        
        res_u_1 = 1*a_u_1[0] 
        for i in range(1, len(a_u_1)):
            res_u_1 += a_u_1[i]*((t-pol_shift)**i)
        
        errint = error_norm(res, res_u_1, atol, rtol)

        h_int = H*((1/errint)**(1/degree))
        
        return (res, errint, h_int)

    return poly

def interpolate_nonsym(y0, Tkk, yj, hs, H, k, atol, rtol,
        seq=(lambda t: 4*t-2)):
    u = k
    u_1 = u - 1

    rs = compute_rs(yj, hs, k, seq=seq)
    
    #a_u are the coefficients for the interpolation polynomial
    a_u = (u+1)*[None]
    a_u_1 = (u_1+1)*[None]
    
    a_u[0] = 1*Tkk
    sumcoeff=0
    for i in range(1,u):
        a_u[i] = (H**i)*rs[i]/math.factorial(i)
        sumcoeff+=(-1)**i*a_u[i]

    a_u[u] = 1/(-1)**u*(y0-a_u[0]-sumcoeff)
    
    a_u_1[0:u_1]=1*a_u[0:u_1];
    a_u_1[u_1]=1/(-1)**u_1*(y0-a_u_1[0]-sumcoeff+(-1)**u_1*a_u[u_1])
    
    return getPolynomial(a_u,a_u_1,H,u,1,atol,rtol)
    

def interpolate_sym(y0, Tkk, f_Tkk, y_half, f_yj, hs, H, k, atol, rtol,
        seq=(lambda t: 4*t-2)):
    u = 2*k-3
    u_1 = u - 1

    ds = compute_ds(y_half, f_yj, hs, k, seq=seq)
    
    a_u = (u+5)*[None]
    a_u_1 = (u_1+5)*[None]
    
    for i in range(u+1):
        a_u[i] = (H**i)*ds[i]/math.factorial(i)

    a_u_1[0:u_1+1] = 1*a_u[0:u_1+1]   

    def A_inv(u):
        return (2**(u-2))*np.matrix(
                [[(-2*(3 + u))*(-1)**u,   -(-1)**u,     2*(3 + u),   -1],
                 [(4*(4 + u))*(-1)**u,     2*(-1)**u,   4*(4 + u),   -2],
                 [(8*(1 + u))*(-1)**u,     4*(-1)**u,  -8*(1 + u),    4],
                 [(-16*(2 + u))*(-1)**u,  -8*(-1)**u,  -16*(2 + u),   8]]
                );
        
        
    A_inv_u = A_inv(u)

    A_inv_u_1 = A_inv(u_1)

    b1_u = 1*y0
    b1_u_1 = 1*y0
    for i in range(u_1+1):
        b1_u -= a_u[i]/(-2)**i
        b1_u_1 -= a_u_1[i]/(-2)**i
    
    b1_u -= a_u[u]/(-2)**u


    b2_u = H*f_yj[1][0]
    b2_u_1 = H*f_yj[1][0]
    for i in range(1, u_1+1):
        b2_u -= i*a_u[i]/(-2)**(i-1)
        b2_u_1 -= i*a_u_1[i]/(-2)**(i-1)

    b2_u -= u*a_u[u]/(-2)**(u-1)
        
        
    b3_u = 1*Tkk
    b3_u_1 = 1*Tkk
    for i in range(u_1+1):
        b3_u -= a_u[i]/(2**i)
        b3_u_1 -= a_u_1[i]/(2**i)

    b3_u -= a_u[u]/(2**u)

    b4_u = H*f_Tkk
    b4_u_1 = H*f_Tkk
    for i in range(1, u_1+1):
        b4_u -= i*a_u[i]/(2**(i-1))
        b4_u_1 -= i*a_u_1[i]/(2**(i-1))

    b4_u -= u*a_u[u]/(2**(u-1))
        
    b_u = np.array([b1_u,b2_u,b3_u,b4_u])
    b_u_1 = np.array([b1_u_1,b2_u_1,b3_u_1,b4_u_1])
    
    x = A_inv_u*b_u
    x = np.array(x)

    x_1 = A_inv_u_1*b_u_1
    x_1 = np.array(x_1)

    a_u[u+1] = x[0]
    a_u[u+2] = x[1]
    a_u[u+3] = x[2]
    a_u[u+4] = x[3]

    a_u_1[u_1+1] = x_1[0]
    a_u_1[u_1+2] = x_1[1]
    a_u_1[u_1+3] = x_1[2]
    a_u_1[u_1+4] = x_1[3]

    # polynomial of degree u+4 defined on [0,1] and centered about 1/2
    # also returns the interpolation error (errint). If errint > 10, then reject
    # step 
    return getPolynomial(a_u,a_u_1,H,u+4,0.5,atol,rtol)
    

def getDenseAndSequence(t_final, t, t_index, seq, symmetric):
    '''
    Returns whether dense output is needed because an intermediate solution
    is wanted at this step. Also chooses the step sequence to use for this step
    
    
    **Parameters**
        - t_final: float
            final time after step is taken
        - t : numpy.ndarray
            All times when output is requested
        - t_index : int
            Index of t ndarray at which next output value is requested
        - seq: callable(k) (k: positive int)
            The step-number sequence used to compute T, if None a default is used
            depending on whether a dense output is needed
    
    **Returns**
        - dense : whether at the interval currently calculated there is intermediate solutions
            to be calculated by dense output
        - seq : step sequence to take for this step
 
    '''
    
    dense=True
    #See if any intermediate points are asked in the interval we are
    #calculating (the value at t_final is not interpolated)

    timeOutRange = t[t_index]>=t_final
    
    if(timeOutRange):
        dense=False
    
    if dense:
        if symmetric:
            seq = lambda t: 2*(2*t-1)     # {2,6,10,14,...} sequence for dense output
        else:
            if(seq is not None):
                return (dense,seq)
            seq = lambda t: 2*(2*t-1) 
    else:
        if(seq is not None):
            return (dense,seq)
        seq = lambda t: 2*(2*t-1)         # harmonic sequence for midpoint method
        
#     seq = lambda t: 2*(2*t-1)
#     seq = lambda t: t+1
    
    return (dense,seq)

#TODO: should be always true for implicit and semiimplicit (they use the Jacobian)
addwork=True
def setwork(count):
    global addwork
    addwork=count

def estimate_next_step_and_order(T, k, h, atol, rtol, seq, adaptative): 
    '''
    Estimates next step and order, and whether to reject step, from the results
    obtained by the solver and the tolerance asked
    
    
    **Parameters**
        - T: numpy.ndarray (bidimensional matrix)
            Extrapolation tableau with all the extrapolation values
        - k : int
            Initial condition on y (can be a vector). Must be a non-scalar 
            numpy.ndarray
        - h : float
            An ordered sequence of time points for which to solve for y. The initial 
            value point should be the first element of this sequence.
        - atol, rtol : float
            The input parameters atol and rtol (absolute tolerance and relative tolerance)
            determine the error control performed by the solver.
        - seq: callable(k) (k: positive int)
            The step-number sequence used to compute T
        - adaptive: string
            Specifies the strategy of integration. Can take two values:
            -- "fixed"  = use fixed step size and fixed order strategy.
            -- "order" = use adaptive step size and adaptive order strategy.
    
    **Returns**
        - rejectStep : whether to reject the step and the solution obtained
            (because it doesn't satisfy the asked tolerances)
        - y : best solution at the end of the step
        - h_new : new step to take
        - k_new :  new order to use
 
    '''
    
    if(adaptative=="fixed"):
        return (False, T[k,k], h, k)
    sizeODE=0
    if(addwork):
        sizeODE = len(T[k,k])
    #Define work function (to minimize)      
    def A_k(k):
        """
           Expected time to compute k lines of the extrapolation table,
           in units of RHS evaluations.
        """
        sum_ = 0
        for i in range(k):
            sum_ += seq(i+1)
        #sizeODE is the length of the ODE
        return max(seq(k), sum_/NUM_WORKERS)+sizeODE # The second value is only an estimate

    H_k = lambda h, k, err_k: h*0.94*(0.65/err_k)**(1/(2*k-1)) 
    W_k = lambda Ak, Hk: Ak/Hk
    
     # compute the error and work function for the stages k-2, k-1 and k
    err_k_2 = error_norm(T[k-2,k-3], T[k-2,k-2], atol, rtol)
    err_k_1 = error_norm(T[k-1,k-2], T[k-1,k-1], atol, rtol)
    err_k   = error_norm(T[k,k-1],   T[k,k],     atol, rtol)
    h_k_2   = H_k(h, k-2, err_k_2)
    h_k_1   = H_k(h, k-1, err_k_1)
    h_k     = H_k(h, k,   err_k)
    w_k_2   = W_k(A_k(k-2), h_k_2)
    w_k_1   = W_k(A_k(k-1), h_k_1)
    w_k     = W_k(A_k(k),   h_k)


    #Order and Step Size Control (II.9 Extrapolation Methods),
    #Solving Ordinary Differential Equations I (Hairer, Norsett & Wanner)
    if err_k_1 <= 1:
        # convergence in line k-1
        if err_k <= 1:
            y = T[k,k]
        else:
            y = T[k-1,k-1]

        k_new = k if w_k_1 < 0.9*w_k_2 else k-1
        h_new = h_k_1 if k_new <= k-1 else h_k_1*A_k(k)/A_k(k-1)
            
        rejectStep=False

    elif err_k <= 1:
        # convergence in line k
        y = T[k,k]

        k_new = k-1 if w_k_1 < 0.9*w_k else (
                k+1 if w_k < 0.9*w_k_1 else k)
        h_new = h_k_1 if k_new == k-1 else (
                h_k if k_new == k else h_k*A_k(k+1)/A_k(k))
            
        rejectStep=False
    else: 
        # no convergence
        # reject (h, k) and restart with new values accordingly
        k_new = k-1 if w_k_1 < 0.9*w_k else k
        h_new = min(h_k_1 if k_new == k-1 else h_k, h)
        y=None
        rejectStep=True
    
    #Extra protection for the cases where there has been an
    #overflow. Make sure the step is rejected (there is cases
    #where err_k_x is not nan
    if(math.isnan(h_new)):
        rejectStep = True
        
    return (rejectStep, y, h_new, k_new)


def solve_one_step(method, methodargs, func, grad, t_curr, t, t_index, yn, args, h, k, atol, rtol, 
                   pool, smoothing, symmetric, seq, adaptative, rejectPreviousStep, previousStepSolution):
    
    dense, seq = getDenseAndSequence(t_curr+h, t, t_index, seq, symmetric)
    
    #Limit k, order of extrapolation
    #If order and step are fixed, do not limit order
    if(not adaptative == 'fixed'):
        k_max = 10
        k_min = 3
        k = min(k_max, max(k_min, k))

    T, fe_seq, fe_tot, je_tot, y0, y_half, f_yj,yj, f_yn, hs = compute_extrapolation_table(method, methodargs, func, grad, 
                t_curr, yn, args, h, k, pool, rejectPreviousStep, previousStepSolution, seq, smoothing, symmetric)
    
    rejectStep, y, h_new, k_new = estimate_next_step_and_order(T, k, h, atol, rtol, seq, adaptative)
    
    y_solution=[]
    if((not rejectStep) & dense):
        #TODO: see if grad (gradient function) can be used for the interpolating
        rejectStep, y_solution, h_int, fe_tot_ = interpolate_values_at_t(func, args, T, k, t_curr, t, t_index, h, hs, y_half, f_yj,yj, y0, 
                                                                         fe_seq, fe_tot, atol, rtol, seq, adaptative, symmetric)
        fe_tot += fe_tot_
        if(not adaptative=="fixed"):
            if(rejectStep):
                h_new = 1*h_int
                #Use same order if step is rejected by the interpolation (do not use the k_new of the adapted order)
                k_new = 1*k  
            elif((h_int is not None) and h_int<h_new):
                h_new = 1*h_int
            

    return (rejectStep, y, y_solution,f_yn, h, k, h_new, k_new, (fe_seq, fe_tot, je_tot))


def interpolate_values_at_t(func, args, T, k, t_curr, t, t_index, h, hs, y_half, f_yj,yj, y0,
                            fe_seq, fe_tot, atol, rtol, seq, adaptative, symmetric):
    
    fe_tot = 0
    #Do last evaluations to construct polynomials
    #they are done here to avoid extra function evaluations if interpolation is not needed
    for j in range(1,len(T[:,1])):
        Tj1=T[j,1]
        f_yjj = f_yj[j]
        #TODO: reuse last function evaluation to calculate next step
        #IMPORTANT!
        #TODO:review implementation, pag238 solving ODEs I seems to only use
        #f at nj/2, but the code seems to need f at nj...
        f_yjj[-1] = func(*(Tj1, t_curr + h) + args)
        fe_tot+=1
        
        
    Tkk = T[k,k]
    f_Tkk = func(*(Tkk, t_curr+h) + args)
    fe_seq +=1
    fe_tot +=1
    
    #Calculate interpolating polynomial
    if(symmetric):
#         poly = interpolate_nonsym(y0, Tkk, yj, hs, h, k, atol,rtol, seq)
        poly = interpolate_sym(y0, Tkk, f_Tkk, y_half, f_yj, hs, h, k, atol,rtol, seq)
    else:
        poly = interpolate_nonsym(y0, Tkk, yj, hs, h, k, atol,rtol, seq)
#         poly = interpolate_sym(y0, Tkk, f_Tkk, y_half, f_yj, hs, h, k, atol,rtol, seq)

    y_solution=[]
    old_index = t_index
    h_int=None

    #Interpolate values at the asked t times in the current range calculated
    while t_index < len(t) and t[t_index] < t_curr + h:
            
        y_poly, errint, h_int = poly((t[t_index] - t_curr)/h)
        
        if adaptative=="fixed":
            y_solution.append(1*y_poly)
            cur_stp = 0
            t_index += 1
        elif errint <= 10:
            y_solution.append(1*y_poly)
            cur_stp = 0
            t_index += 1
        else:
            h = h_int
            t_index = old_index
            rejectStep=True
            return (rejectStep, y_solution, h_int, fe_tot)
             
    rejectStep=False
    return (rejectStep, y_solution, h_int, fe_tot)

def ex_midpoint_explicit_parallel(func, grad, y0, t, args=(), full_output=0, rtol=1.0e-8,
        atol=1.0e-8, h0=0.5, mxstep=10e4, robustness=2, smoothing = 'no', seq=None, useGradient=False, p=4, 
        nworkers=None, adaptative="order"):
    '''
    (An instantiation of extrapolation_parallel() function with the midpoint 
    method.)
    
    Solves the system of IVPs dy/dt = func(y, t0, ...) with parallel extrapolation. 
    
    **Parameters**
        - func: callable(y, t0, ...)
            Computes the derivative of y at t0 (i.e. the right hand side of the 
            IVP). Must output a non-scalar numpy.ndarray
        - y0 : numpy.ndarray
            Initial condition on y (can be a vector). Must be a non-scalar 
            numpy.ndarray
        - t : array
            An ordered sequence of time points for which to solve for y. The initial 
            value point should be the first element of this sequence.
        - args : tuple, optional
            Extra arguments to pass to function.
        - full_output : bool, optional
            True if to return a dictionary of optional outputs as the second 
            output. Defaults to False
        - adaptive: string, optional
            Specifies the strategy of integration. Can take three values:
            -- "fixed"  = use fixed step size but fixed order strategy.
            -- "order" = use adaptive step size and adaptive order strategy.
            Defaults to "order"
    
    IMPORTANT: all input parameters should be float types, this solver doesn't work
    properly if for example y0 is not a float but an int variable (i.e. 1 instead of 1.)
    TODO: add protection so that int inputs are formated to floats (and algorithm works)
    
    **Returns**
        - ys : numpy.ndarray, shape (len(t), len(y0))
            Array containing the value of y for each desired time in t, with 
            the initial value y0 in the first row.
        - infodict : dict, only returned if full_output == True
            Dictionary containing additional output information
            KEY         MEANING
            'fe_seq'    cumulative number of sequential derivative evaluations
            'nfe'       cumulative number of total derivative evaluations
            'nst'       cumulative number of successful time steps
            'nje'       cumulative number of either Jacobian evaluations (when 
                        analytic Jacobian is provided) or Jacobian estimations
                        (when no analytic Jacobian is provided)
            'h_avg'     average step size if adaptive == "order" (None otherwise)
            'k_avg'     average extrapolation order if adaptive == "order" 
                        ... (None otherwise)

    **Other Parameters**
        - rtol, atol : float, optional
            The input parameters rtol and atol determine the error control 
            performed by the solver. The solver will control the vector, 
            e = y2 - y1, of estimated local errors in y, according to an 
            inequality of the form l2-norm of (e / (ewt * len(e))) <= 1, 
            where ewt is a vector of positive error weights computed as 
            ewt = atol + max(y1, y2) * rtol. rtol and atol can be either vectors
            the same length as y0 or scalars. Both default to 1.0e-8.
        - h0 : float, optional
            The step size to be attempted on the first step. Defaults to 0.5
        - mxstep : int, optional
            Maximum number of (internally defined) steps allowed for each
            integration point in t. Defaults to 10e4
            Defaults to "order"
        - p: int, optional
            The order of extrapolation if adaptive is not "order", and the 
            starting order otherwise. Defaults to 4
        - nworkers: int, optional
            The number of workers working in parallel. If nworkers==None, then 
            the the number of workers is set to the number of CPUs on the the
            running machine. Defaults to None.
    '''
    global useGrad
    useGrad = useGradient
    
    method = midpoint_explicit
    
    k=p//2

    return extrapolation_parallel(method,  {}, func, grad, y0, t, args=args,
        full_output=full_output, rtol=rtol, atol=atol, h0=h0, mxstep=mxstep, robustness_factor=robustness,
         p=k, nworkers=nworkers, smoothing=smoothing, symmetric=True, seq=seq, adaptative=adaptative)

def ex_midpoint_implicit_parallel(func, grad, y0, t, args=(), full_output=0, rtol=1.0e-8,
        atol=1.0e-8, h0=0.5, mxstep=10e4, robustness=2, smoothing = 'no', seq=None, useGradient=False, p=4,
        nworkers=None, adaptative="order"):
    
    method = midpoint_implicit
    
    k=p//2
    
    global useGrad
    useGrad = useGradient

    return extrapolation_parallel(method, {}, func, grad, y0, t, args=args,
        full_output=full_output, rtol=rtol, atol=atol, h0=h0, mxstep=mxstep, robustness_factor=robustness,
         p=k, nworkers=nworkers, smoothing=smoothing, symmetric=True, seq=seq, adaptative=adaptative)


def ex_midpoint_semi_implicit_parallel(func, grad, y0, t, args=(), full_output=0, rtol=1.0e-8,
        atol=1.0e-8, h0=0.5, mxstep=10e4, robustness=2, smoothing = 'no', seq=None, useGradient=False, p=4,
        nworkers=None, adaptative="order"):
    
    method = midpoint_semiimplicit
    
    k=p//2
    
    global useGrad
    useGrad = useGradient
    
    methodargs={}
    methodargs["J00"]=None

    return extrapolation_parallel(method, methodargs, func, grad, y0, t, args=args,
        full_output=full_output, rtol=rtol, atol=atol, h0=h0, mxstep=mxstep, robustness_factor=robustness,
         p=k, nworkers=nworkers, smoothing=smoothing, symmetric = True, seq=seq, adaptative=adaptative)

def ex_euler_explicit_parallel(func, grad, y0, t, args=(), full_output=0, rtol=1.0e-8,
        atol=1.0e-8, h0=0.5, mxstep=10e4, robustness=2, smoothing = 'no', seq=None, p=4,
        nworkers=None, adaptative="order"):
    
    method = euler_explicit

    return extrapolation_parallel(method, {}, func, grad, y0, t, args=args,
        full_output=full_output, rtol=rtol, atol=atol, h0=h0, mxstep=mxstep, robustness_factor=robustness,
         p=p, nworkers=nworkers, smoothing=smoothing, symmetric = False, seq=seq, adaptative=adaptative)
    
def ex_euler_semi_implicit_parallel(func, grad, y0, t, args=(), full_output=0, rtol=1.0e-8,
        atol=1.0e-8, h0=0.5, mxstep=10e4, robustness=2, smoothing = 'no', seq=None, useGradient=False,p=4,
        nworkers=None, adaptative="order"):
    
    method = euler_semiimplicit
    
    global useGrad
    useGrad = useGradient
    
    methodargs={}
    methodargs["J00"]=None

    return extrapolation_parallel(method, methodargs, func, grad, y0, t, args=args,
        full_output=full_output, rtol=rtol, atol=atol, h0=h0, mxstep=mxstep, robustness_factor=robustness,
         p=p, nworkers=nworkers, smoothing=smoothing, symmetric = False, seq=seq, adaptative=adaptative)
