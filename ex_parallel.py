from __future__ import division
import numpy as np
import multiprocessing as mp
import math
from scipy import optimize
import os

NUM_WORKERS = None

def set_NUM_WORKERS(nworkers):
    global NUM_WORKERS
    if nworkers == None:
        try:
            NUM_WORKERS = mp.cpu_count()
        except NotImplementedError:
            NUM_WORKERS = 4
    else: 
        NUM_WORKERS = max(nworkers, 1)

def error_norm(y1, y2, atol, rtol):
    tol = atol + np.maximum(y1,y2)*rtol
    return np.linalg.norm((y1-y2)/tol)/(len(y1)**0.5)

'''''
BEG: ODE numerical methods formulas (explicit and implicit)

Methods structure:
def method_implicit/explicit(f,previousValue/previousValues,previousTime,step)
    @param f: derivative of u(t) (ODE RHS, f(u,t))
    @param previousValues: previous solution values (two previous values) 
    used to obtain the next point (using two previous values because midpoint explicit
    method needs the previous two points, otherwise the t_n-2 value can remain unused)
    @param previousTime: time at which previous solution is found
    @param step: step length
    
    @return: implicit -> return a function to find the root of,
    explicit -> return the next estimated solution value and the f evaluation
'''''

def solve_implicit_step(zero_f):
    '''
    
    @param zero_f: function to find the root of (estimation of next step)
    '''
    estimatedValueExplicit=previousValue+step/2*f(previousValue)
    return optimize.newton_krylov(zero_f,estimatedValueExplicit)

def midpoint_implicit(f,previousValues, previousTime,step, args):
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
    
    return (solve_implicit_step(zero_func), None)


def midpoint_explicit(f,previousValues, previousTime, step, args):
    """
    Creates midpoint function method formula u_n+1=u_n+h*f(
    
    @param f: derivative of u(t) (ODE RHS, f(u,t))
    @param previousValues: previous solution values used to obtain of next point
    @param previousTime: time at which previous solution is found
    @param step: step length
    
    @return estimated value at previousTime + step
    """
    previousPreviousValue, previousValue = previousValues
    
    if(previousPreviousValue is None):
        return euler_explicit(f, previousValues, previousTime, step, args)
    
    f_yj = f(*(previousValue, previousTime)+args)
    return (previousPreviousValue + (2*step)*f_yj, f_yj)

def euler_explicit(f,previousValues, previousTime,step, args):
    previousPreviousValue, previousValue = previousValues
    f_yj = f(*(previousValue, previousTime)+args)
    return (previousValue + step*f_yj, f_yj)


'''''
END: ODE numerical methods formulas (explicit and implicit)
'''''

def compute_stages((method, func, tn, yn, args, h, k_nj_lst)):
    res = []
    for (k,nj) in k_nj_lst:
        nj = int(nj)
        Y = np.zeros((nj+1, len(yn)), dtype=(type(yn[0])))
        f_yj = np.zeros((nj+1, len(yn)), dtype=(type(yn[0])))
        Y[0] = yn
        step = h/nj

        Y[1],f_yj[0] = method(func,(None, Y[0]), tn, step, args)
        for j in range(2,nj+1):
            Y[j],f_yj[j-1] = method(func,(Y[j-2], Y[j-1]), tn + (j-1)*(h/nj), step, args)
        y_half = Y[nj/2]
        res += [(k, nj, Y[nj], y_half, f_yj)]

    return res

def extrapolation_parallel (method, func, y0, t, args=(), full_output=False,
        rtol=1.0e-8, atol=1.0e-8, h0=0.5, mxstep=10e4, p=4,
        nworkers=None):
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

    #Initialize pool of workers to parallelize extrapolation table calculations
    set_NUM_WORKERS(nworkers)  
    pool = mp.Pool(NUM_WORKERS)

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
    nstp = 0
    cur_stp = 0

    t_max = t[-1]
    t_index = 1

    #yn is the previous calculated step (at t_curr)
    yn, t_curr, k = 1*y0, t0, p
    h = min(h0, t_max-t0)

    sum_ks, sum_hs = 0, 0

    #Iterate until you reach final time
    while t_curr < t_max:   
        rejectStep, y_temp, ysolution, h, k, h_new, k_new, (fe_seq_, fe_tot_) = solve_one_step(
                method, func, t_curr, t, t_index, yn, args, h, k, atol, rtol, pool)
        
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

        sum_ks += k
        sum_hs += h
        nstp += 1
        cur_stp += 1

        if cur_stp > mxstep:
            raise Exception('Reached Max Number of Steps. Current t = ' 
                + str(t_curr))

        h = min(h_new, t_max - t_curr)
        k = k_new

    #Close pool of workers and return results
    pool.close()

    if full_output:
        infodict = {'fe_seq': fe_seq, 'fe_tot': fe_tot, 'nstp': nstp, 
                    'h_avg': sum_hs/nstp, 'k_avg': sum_ks/nstp}
        return (ys, infodict)
    else:
        return ys

def balance_load(k, seq=(lambda t: 2*t)):
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

    fe_tot = 0 
    for i in range(len(k_nj_lst)):
        fe_tot += sum([pair[1] for pair in k_nj_lst[i]])
    
    fe_seq = sum([pair[1] for pair in k_nj_lst[0]])

    return (k_nj_lst, fe_seq, fe_tot)

def compute_extrapolation_table(method, func, tn, yn, args, h, k, pool, seq=(lambda t: 2*t)):
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
    k_nj_lst, fe_seq, fe_tot = balance_load(k, seq=seq)
    jobs = [(method, func, tn, yn, args, h, k_nj) for k_nj in k_nj_lst]

    results = pool.map(compute_stages, jobs, chunksize=1)

    # process the returned results from the pool 

    y_half = (k+1)*[None]
    f_yj = (k+1)*[None]
    hs = (k+1)*[None]
    for res in results:
        for (k_, nj_, Tk_, y_half_, f_yj_) in res:
            T[k_, 1] = Tk_
            y_half[k_] = y_half_
            f_yj[k_] = f_yj_
            hs[k_] = h/nj_

    fill_extrapolation_table(T,k,seq)

    return (T, fe_seq, fe_tot, yn, y_half, f_yj, hs)

def fill_extrapolation_table(T,k,seq):
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
    # only correct for midpoint method, use for non-symmetric methods:
    #T[j,i] = T[j,i-1] + (T[j,i-1] - T[j-1,i-1])/((seq(j)/(seq(j-i+1))) - 1)
    for i in range(2, k+1):
        for j in range(i, k+1):
            T[j,i] = T[j,i-1] + (T[j,i-1] - T[j-1,i-1])/((seq(j)/(seq(j-i+1)))**2 - 1)
            
def finite_diff(j, f_yj, hj):
    # Called by interpolate
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

def compute_ds(y_half, f_yj, hs, k, seq=(lambda t: 4*t-2)):
    # Called by interpolate
    dj_kappa = np.zeros((2*k+1, k+1), dtype=(type(y_half[1])))
    ds = np.zeros((2*k+1), dtype=(type(y_half[1])))
    
    for j in range(1,k+1):
        dj_kappa[0,j] = 1*y_half[j]
        nj = len(f_yj[j])-1
        dj_ = finite_diff(j,f_yj[j], hs[j])
        for kappa in range(1,2*j+1):    
            dj_kappa[kappa,j] = 1*dj_[kappa]

    skip = 0
    for kappa in range(2*k+1):
        T = np.zeros((k+1-int(skip/2), k+1 - int(skip/2)), dtype=(type(y_half[1])))
        T[:,1] = 1*dj_kappa[kappa, int(skip/2):]

        for i in range(2, k+1-int(skip/2)):
            for j in range(i, k+1-int(skip/2)):
                T[j,i] = T[j,i-1] + (T[j,i-1] - T[j-1,i-1])/((seq(j)/(seq(j-i+1)))**2 - 1)
        ds[kappa] = 1*T[k-int(skip/2),k-int(skip/2)] 
        if not(kappa == 0):
            skip +=1

    return ds 

def interpolate(y0, Tkk, f_Tkk, y_half, f_yj, hs, H, k, atol, rtol,
        seq=(lambda t: 4*t-2)):
    u = 2*k-3
    u_1 = u - 1
    ds = compute_ds(y_half, f_yj, hs, k, seq=seq)
    
    a_u = (u+5)*[None]
    a_u_1 = (u_1+5)*[None]
    
    for i in range(u+1):
        a_u[i] = (H**i)*ds[i]/math.factorial(i)
    for i in range(u_1 + 1):
        a_u_1[i] = (H**i)*ds[i]/math.factorial(i)

    A_inv_u = (2**(u-2))*np.matrix(
                [[(-2*(3 + u))*(-1)**u,   -(-1)**u,     2*(3 + u),   -1],
                 [(4*(4 + u))*(-1)**u,     2*(-1)**u,   4*(4 + u),   -2],
                 [(8*(1 + u))*(-1)**u,     4*(-1)**u,  -8*(1 + u),    4],
                 [(-16*(2 + u))*(-1)**u,  -8*(-1)**u,  -16*(2 + u),   8]]
                ) 

    A_inv_u_1 = (2**(u_1-2))*np.matrix(
                [[(-2*(3 + u_1))*(-1)**u_1,   -(-1)**u_1,     2*(3 + u_1),  -1], 
                 [(4*(4 + u_1))*(-1)**u_1,     2*(-1)**u_1,   4*(4 + u_1),  -2], 
                 [(8*(1 + u_1))*(-1)**u_1,     4*(-1)**u_1,  -8*(1 + u_1),   4], 
                 [(-16*(2 + u_1))*(-1)**u_1,  -8*(-1)**u_1,  -16*(2 + u_1),  8]]
                ) 

    b1_u = 1*y0
    for i in range(u+1):
        b1_u -= a_u[i]/(-2)**i

    b1_u_1 = 1*y0
    for i in range(u_1+1):
        b1_u_1 -= a_u_1[i]/(-2)**i

    b2_u = H*f_yj[1][0]
    for i in range(1, u+1):
        b2_u -= i*a_u[i]/(-2)**(i-1)

    b2_u_1 = H*f_yj[1][0]
    for i in range(1, u_1+1):
        b2_u_1 -= i*a_u_1[i]/(-2)**(i-1)

    b3_u = 1*Tkk
    for i in range(u+1):
        b3_u -= a_u[i]/(2**i)

    b3_u_1 = 1*Tkk
    for i in range(u_1+1):
        b3_u_1 -= a_u_1[i]/(2**i)

    b4_u = H*f_Tkk
    for i in range(1, u+1):
        b4_u -= i*a_u[i]/(2**(i-1))

    b4_u_1 = H*f_Tkk
    for i in range(1, u_1+1):
        b4_u_1 -= i*a_u_1[i]/(2**(i-1))

    b_u = np.array([b1_u,b2_u,b3_u,b4_u])
    b_u_1 = np.array([b1_u_1,b2_u_1,b3_u_1,b4_u_1])

    x = A_inv_u*b_u
    x = np.array(x)

    x_1 = A_inv_u_1*b_u_1
    x_1 = np.array(x)

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
    def poly (t):
        res = 1*a_u[0] 
        for i in range(1, len(a_u)):
            res += a_u[i]*((t-0.5)**i)
        
        res_u_1 = 1*a_u_1[0] 
        for i in range(1, len(a_u_1)):
            res_u_1 += a_u_1[i]*((t-0.5)**i)
        
        errint = error_norm(res, res_u_1, atol, rtol)
        h_int = H*((1/errint)**(1/(u+4)))
        
        return (res, errint, h_int)

    return poly
    

def getStepAndSequence(t_final, t, t_index):
    dense=True
    #See if any intermediate points are asked in the interval we are
    #calculating (the value at t_final is not interpolated)
    if(t[t_index]>=t_final):
        dense=False
    
    if dense:
        seq = lambda t: 4*t - 2     # {2,6,10,14,...} sequence for dense output
    else:
        seq = lambda t: 2*t         # harmonic sequence for midpoint method
    return (dense,seq)

def estimate_next_step_and_order(T, k, h, atol, rtol, seq): 
    #Define work function (to minimize)      
    def A_k(k):
        """
           Expected time to compute k lines of the extrapolation table,
           in units of RHS evaluations.
        """
        sum_ = 0
        for i in range(k):
            sum_ += seq(i+1)
        return max(seq(k), sum_/NUM_WORKERS) # The second value is only an estimate

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
    
    return (rejectStep, y, h_new, k_new)


def solve_one_step(method, func, t_curr, t, t_index, yn, args, h, k, atol, rtol, pool):
    
    dense, seq = getStepAndSequence(t_curr+h, t, t_index)
    
    #Limit k, order of extrapolation
    k_max = 10
    k_min = 3
    k = min(k_max, max(k_min, k))

    T, fe_seq, fe_tot, y0, y_half, f_yj, hs = compute_extrapolation_table(
            method, func, t_curr, yn, args, h, k, pool, seq)
    
    rejectStep, y, h_new, k_new = estimate_next_step_and_order(T, k, h, atol, rtol, seq)
    
    y_solution=[]
    if((not rejectStep) & dense):
        rejectStep, y_solution, h_int = interpolate_values_at_t(func, args, T, k, t_curr, t, t_index, h, hs, y_half, f_yj, y0, fe_seq, fe_tot, atol, rtol, seq)
        if(rejectStep):
            h_new = h_int
            #Use same order if step is rejected by the interpolation (do not use the k_new of the adapted order)
            k_new = k  

    return (rejectStep, y, y_solution, h, k, h_new, k_new, (fe_seq, fe_tot))


def interpolate_values_at_t(func, args, T, k, t_curr, t, t_index, h, hs, y_half, f_yj, y0, fe_seq, fe_tot, atol, rtol, seq):
    
    #Do last evaluations to construct polynomials
    #they are done here to avoid extra function evaluations if interpolation is not needed
    for j in range(1,len(T[:,1])):
        Tj1=T[j,1]
        f_yjj = f_yj[j]
        f_yjj[-1] = func(*(Tj1, t_curr + h) + args)
        
    Tkk = T[k,k]
    f_Tkk = func(*(Tkk, t_curr+h) + args)
    fe_seq +=1
    fe_tot +=1
    
    #Calculate interpolating polynomial
    poly = interpolate(y0, Tkk, f_Tkk, y_half, f_yj, hs, h, k, atol,
        rtol, seq)

    y_solution=[]
    old_index = t_index
    h_int=None
    #Interpolate values at the asked t times in the current range calculated
    while t_index < len(t) and t[t_index] < t_curr + h:
        y_poly, errint, h_int = poly((t[t_index] - t_curr)/h)
        
        if errint <= 10:
            y_solution.append(1*y_poly)
            cur_stp = 0
            t_index += 1
        else:
            h = h_int
            #TODO: see this fe_seq is properly calculated
#             fe_seq += fe_seq_
#             fe_tot += fe_tot_
            t_index = old_index
            rejectStep=True
            return (rejectStep, y_solution, h_int)
        
    rejectStep=False
    return (rejectStep, y_solution, h_int)

def ex_midpoint_explicit_parallel(func, y0, t, args=(), full_output=0, rtol=1.0e-8,
        atol=1.0e-8, h0=0.5, mxstep=10e4, p=4, nworkers=None):
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
        - nworkers: int, optional
            The number of workers working in parallel. If nworkers==None, then 
            the the number of workers is set to the number of CPUs on the the
            running machine. Defaults to None.
    '''

    method = midpoint_explicit

    return extrapolation_parallel(method, func, y0, t, args=args,
        full_output=full_output, rtol=rtol, atol=atol, h0=h0, mxstep=mxstep,
         p=p, nworkers=nworkers)

def ex_midpoint_implicit_parallel(func, y0, t, args=(), full_output=0, rtol=1.0e-8,
        atol=1.0e-8, h0=0.5, mxstep=10e4, p=4, nworkers=None):

    method = midpoint_implicit

    return extrapolation_parallel(method, func, y0, t, args=args,
        full_output=full_output, rtol=rtol, atol=atol, h0=h0, mxstep=mxstep,
         p=p, nworkers=nworkers)

'''''
Deprecated code    
    (to be removed)
'''''

def compute_stages_implicit((func, tn, yn, args, h, k_nj_lst)):
    res = []
#     print(str(os.getpid()) + " " +"entra")
    for (k, nj) in k_nj_lst:
        nj = int(nj)
        Y = np.zeros((nj+1, len(yn)), dtype=(type(yn[0])))
        Y[0] = yn
        #Use midpoint method to calculate point at tn+h using nj intermediate points
        for j in range(1,nj+1):
            previousValue = Y[j-1]
            step = h/nj
            #Check if this is better for newton initial value:
            estimatedValueExplicit=previousValue+step*func(previousValue,tn + (j-1)*(h/nj))
            #TODO: add same tolerance as problems tolerance for finding the root
            Y[j] = optimize.fsolve(midpoint_zero_function(func, previousValue, tn + (j-1)*(h/nj), step),estimatedValueExplicit)
#             print(str(os.getpid()) + " " + str(Y[j]))
        res += [(k, nj, Y[nj])]

    return res

#TODO: remove, unused, the same as compute_stages_dense
def compute_stages_unused((func, tn, yn, args, h, k_nj_lst)):
    res = []
    #k, nj = k_nj[0]
    for (k,nj) in k_nj_lst:
        nj = int(nj)
        Y = np.zeros((nj+1, len(yn)), dtype=(type(yn[0])))
        Y[0] = yn
        Y[1] = Y[0] + h/nj*func(*(Y[0], tn) +args)
        #Use midpoint method to calculate point at tn+h using nj intermediate points
        for j in range(2,nj+1):
            Y[j] = Y[j-2] + (2*h/nj)*func(*(Y[j-1], tn + (j-1)*(h/nj))+ args)
#             print(str(os.getpid()) + " " + str(Y[j]))
        res += [(k, nj, Y[nj],None,None)]

    return res
def midpoint_fixed_step(func, tn, yn, args, h, p, pool, seq=(lambda t: 2*t),
        dense=False):
    k = int(round(p/2))
    if dense:
        T, fe_seq, fe_tot, y0, Tkk, f_Tkk, y_half, f_yj, hs = compute_extrapolation_table(
            func, tn, yn, args, h, k, pool, seq=seq, dense=dense)
        poly = interpolate(y0, Tkk, f_Tkk, y_half, f_yj, hs, h, k, atol, rtol,
            seq=seq)
        return (T[k,k], T[k-1,k-1], (fe_seq, fe_tot), poly)
    else:
        T, fe_seq, fe_tot = compute_extrapolation_table(func, tn, yn, args, h, k, pool,
            seq=seq, dense=dense)
        return (T[k,k], T[k-1,k-1], (fe_seq, fe_tot))
def adapt_step(method, func, tn_1, yn_1, args, y, y_hat, h, p, atol, rtol, pool,
        seq=(lambda t: 2*t), dense=False):
    '''
        Only called when adaptive == 'step'; i.e., for fixed order.

        Checks if the step size is accepted. If not, computes a new step size
        and checks again. Repeats until step size is accepted 

        **Inputs**:
            - method:   -- the method on which the extrapolation is based
            - func      -- the right hand side function of the IVP.
                        Must output a non-scalar numpy.ndarray
            - tn_1, yn_1 -- y(tn_1) = yn_1 is the last accepted value of the 
                        computed solution 
            - args      -- Extra arguments to pass to function.
            - y, y_hat  -- the computed values of y(tn_1 + h) of order p and 
                        (p-1), respectively
            - h         -- the step size taken and to be tested
            - p         -- the order of the higher extrapolation method
                        Assumed to be greater than 1.
            - atol, rtol -- the absolute and relative tolerance of the local 
                         error.
            - seq       -- the step-number sequence. optional; defaults to the 
                        harmonic sequence given by (lambda t: 2*t)

        **Outputs**:
            - y, y_hat  -- the computed solution of orders p and (p-1) at the
                        accepted step size
            - h         -- the accepted step taken to compute y and y_hat
            - h_new     -- the proposed next step size
            - (fe_seq, fe_tot) -- the number of sequential f evaluations, and
                                    the total number of f evaluations
    ''' 

    facmax = 5
    facmin = 0.2
    fac = 0.8
    err = error_norm(y, y_hat, atol, rtol)
    h_new = h*min(facmax, max(facmin, fac*((1/err)**(1/p))))

    fe_seq = 0
    fe_tot = 0

    while err > 1:
        h = h_new
        if dense:
            y, y_hat, (fe_seq_, fe_tot_), poly = method(func, tn_1, yn_1, args,
                h, p, pool, seq=seq, dense=dense)
        else:
            y, y_hat, (fe_seq_, fe_tot_) = method(func, tn_1, yn_1, args, h, p, 
                pool, seq=seq, dense=dense)
        fe_seq += fe_seq_
        fe_tot += fe_tot_
        err = error_norm(y, y_hat, atol, rtol)
        h_new = h*min(facmax, max(facmin, fac*((1/err)**(1/p))))

    if dense:
        return (y, y_hat, h, h_new, (fe_seq, fe_tot), poly)
    else:
        return (y, y_hat, h, h_new, (fe_seq, fe_tot))

