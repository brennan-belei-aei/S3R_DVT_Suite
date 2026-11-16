import tkinter.filedialog
from tkinter import *
from time import *
import numpy as np
import matplotlib.pyplot as plt
import scipy.interpolate
from MISC.helperFunctions import *
from EverestBusCommands import everest

# sp = 25


print('\nChoose DPDZ data files')
root = tkinter.Tk()
root.withdraw()
tfh = tkinter.filedialog.askopenfiles(parent=root,mode='r', initialdir=r'\\aedcsrv01\Product\RF Delivery\Everest\DVT\02 Customer Shipment Testing\3540 CST Data',title='Choose DPDZ data files')

R_plot, X_plot = np.meshgrid(np.linspace(-1, 1, 500), np.linspace(-1, 1, 500))
frequencies = []

gen = everest.Everest('172.24.249.9')
info = unitInfoString(gen)
gen.close()

for f in tfh:
    data = np.genfromtxt(f, delimiter=',', skip_header=True)

    outPath = f.name[0:len(f.name)-47]

    gamma_plot = []
    pdlv_plot = []

    for i in range(len(data)):
        gamma_plot.append([data[i][1], data[i][2]])
        pdlv_plot.append(data[i][3])

    freq = int(f.name[-42:-37]) / 1000
    sp = int(f.name[-33:-30])
    # freq = 40.68
    # sp = 2000
    data_2D = scipy.interpolate.griddata(gamma_plot, pdlv_plot, (R_plot, X_plot))

    Z0 = 50
    vswr = [2, 3, 5]
    # unit circle outline
    theta = np.linspace(0, 2*np.pi, 100)
    x1 = np.cos(theta)
    y1 = np.sin(theta)

    R = [10, 25, 50, 100, 250]
    X = [-250, -100, -50, -25, -10, 0, 10, 25, 50, 100, 250]
    R1 = np.arange(0, np.max(R), 10)


    plt.figure(figsize=(13,11))
    plt.plot(x1,y1,'k-')
    plt.axis('off')

    
    for i in range(len(R)):
        g = (R[i] - Z0)/(R[i] + Z0)
        center = (1+g)/2
        r = 1 - center
        x = center + r*np.cos(theta)
        y = r*np.sin(theta)
        plt.plot(x,y, 'b--', alpha=0.1)
        plt.text(x=g+0.02, y=0, s=R[i], fontsize=10)

    
    for i in range(len(X)):
        Z = R1 + 1j*X[i]
        gamma = (Z - Z0)/(Z + Z0)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'b--', alpha=0.1)
        if np.min(x) > 0 and np.min(y) < 0:
            plt.text(x=np.min(x)+0.125, y=np.min(y)-0.04, s=X[i], fontsize=10)
        elif np.min(x) < 0 and np.min(y) < 0:
            plt.text(x=np.min(x)-0.12, y=np.min(y)-0.04, s=X[i], fontsize=10)        
        elif np.min(x) > 0 and np.max(y) > 0:
            plt.text(x=np.min(x)+0.125, y=np.max(y)+0.03, s=X[i], fontsize=10)
        elif np.min(x) < 0 and np.max(y) > 0:
            plt.text(x=np.min(x)-0.12, y=np.max(y)+0.03, s=X[i], fontsize=10)    
        elif np.min(x) == 0:
            if np.min(y) < 0:
                plt.text(x=-0.05, y=-1.06, s=-50, fontsize=10)
            # if np.max(y) > 0:
            #     plt.text(x=-0.03, y=1.03, s=50, fontsize=10)
        else:
            plt.text(x=-1.075, y=0, s=0, fontsize=10)

    for i in range(len(vswr)):
        gamma = (vswr[i] -  1)/(vswr[i] + 1) * np.exp(1j*theta)
        x = np.real(gamma)
        y = np.imag(gamma)
        plt.plot(x, y, 'r--', alpha=0.2)
        plt.text(x=-0.1, y=np.max(x)+0.02, s='$\\rho$ = {}'.format(vswr[i]))

    
    plt.title('DPDZ  {}MHz {}W Setpoint\nEverest {}'.format(freq, sp, info), fontsize=11)
    plt.contourf(R_plot, X_plot, data_2D, levels=int(len(pdlv_plot)/4), cmap='jet')
    plt.colorbar(label='Watts')

    plt.savefig(outPath+r'DPDZ_{}KHz_{}W.png'.format(freq*1000, sp))
    plt.close()
    # plt.show()