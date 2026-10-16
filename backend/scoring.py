import math

def update(previous,q,n):
    if not isinstance(n,int) or n<1 or not math.isfinite(q) or not 0<=q<=100:raise ValueError('Invalid score or attempt number')
    if n==1:
        if previous is not None:raise ValueError('First attempt has no previous score')
        return .8*q
    if previous is None or not math.isfinite(previous) or not 0<=previous<=100:raise ValueError('Previous score required')
    alpha=.5/math.sqrt(n)*(.5 if previous>=80 else 1)
    return previous+alpha*(q-previous)

def display(score):return math.floor(score*10)/10
