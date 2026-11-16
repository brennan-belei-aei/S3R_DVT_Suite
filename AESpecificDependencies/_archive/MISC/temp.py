import numpy as np

def rect2Polar(x, y):
    Z0 = complex(50, 0)
    Zm = complex(x, y)
    gamma = (Zm - Z0)/(Zm + Z0)
    return np.real(gamma), np.imag(gamma)

temp = [complex(-0.0047850,- 56.708), complex(-0.045858,- 50.305), complex(0.039955, - 44.998)]

for idx in range(3):
    print(rect2Polar(np.real(temp[idx]), np.imag(temp[idx])))