#
# ICRAR - International Centre for Radio Astronomy Research
# (c) UWA - The University of Western Australia, 2018
# Copyright by UWA (in the framework of the ICRAR)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import functools

import numpy as np
import os
import h5py

import common
import utilities_statistics as us

##################################
# Constants
mlow = 18.0
mupp = 28.0
dm = 0.25
mbins = np.arange(mlow, mupp, dm)
xmf = mbins + dm/2.0

zsun = 0.0189
MpctoKpc = 1e3
PI       = 3.141592654
h        = 6.6261e-27 #cm2 g s-1
d10pc = 3.086e+19 #10 parsecs in cm
dfac = 4 * PI * d10pc**2

#model of Mattson et al. (2014) for the dependence of the dust-to-metal mass ratio and metallicity X/H.
corrfactor_dm = 2.0
polyfit_dm = [ 0.00544948, 0.00356938, -0.07893235,  0.05204814,  0.49353238] 

#choose radio continuum model
radio_model = "Bressan02"
c_speed = 299792458.0 #in m/s
c_speed_cm = 299792458.0 * 1e2 #in cm/s
Lsunwatts = 3.846e26

#define parameters associated to AGN luminosity
alpha_adaf = 0.1
delta = 0.0005
beta = 1 - alpha_adaf / 0.55
eta_edd = 4
mcrit_nu = 0.001 * (delta / 0.0005) * (1 - beta) / beta * alpha_adaf**2
spin = 0.1
A_adaf = 5e-4
A_td = A_adaf/100


def ionising_photons(m, wave):
    #m is a vector of AB absolute magnitudes in a band with central wavelength wave
    #wavelength input has to be in angstrom
    
    wave_m = wave * 1e-10 #wavelength in m
    wave_cm = wave_m  * 1e2 #wavelength in cm
    freq = c_speed / wave_m #Hz
    hc =  h * (c_speed * 1e2) #h*c in cgs

    lum = 10.0**((m + 48.6) / (-2.5)) * dfac * freq * wave_cm #we want to convert from Luminosity to int(lambda*Lum_lambda*dlambda)
    Q = lum / hc #rate of ionising photons in s^-1.
  
    return Q

def freefree_lum(Q, nu):

    #Q is the rate of ionising photos in s^-1
    #nu is the frequency in GHz
    #output in erg/s/Hz

    T = 1e4 #temperature in K
    lum = Q/6.3e25 * (T/1e4)**0.45 * (nu)**(-0.1)

    return lum

def synchrotron_lum(SFR, nu):
   
    #SFR in Msun/yr
    #nu is the frequency in GHz
    #output in erg/s/Hz

    ENT = 1.944
    ESNR = 0.06 * ENT
    alpha = -0.8

    comp1 = ESNR * (nu / 1.49)**(-0.5) + ENT * (nu / 1.49)**(alpha)
    nuSNCC = SFR * 0.0095
    lum = comp1 * 1e30 * nuSNCC

    return lum


def radio_luminosity_agn(mbh, macc):

    #input mbh has to be in Msun
    #input macc has to be in Msun/yr
    #output luminosity in erg/s
   
    Ljet_ADAF = np.zeros ( shape = len(mbh))
    Ljet_td = np.zeros ( shape = len(mbh))
    mdot_norm = np.zeros ( shape = len(mbh))
    Ledd = np.zeros ( shape = len(mbh))

    ind = np.where(mbh > 0)
    Ledd[ind] = 1.28e46 * (mbh[ind]/1e8) #in erg/s

    ind = np.where((mbh > 0) & (macc > 0))
    mdot_edd = Ledd[ind] / (0.1 * c_speed_cm**2) * 1.586606334841629e-26 #in Msun/yr
    mdot_norm[ind] = macc[ind] / mdot_edd

    Ljet_ADAF[ind] = 2e45 * (mbh[ind]/1e9) * (mdot_norm[ind]/0.01) * spin**2 #in erg/s
    Ljet_td[ind] = 2.5e43 * (mbh[ind]/1e9)**1.1 * (mdot_norm[ind]/0.01)**1.2 * spin**2 #in erg/s

    ind = np.where(mdot_norm > eta_edd)
    mdot_norm[ind] = eta_edd

    return (Ljet_ADAF, Ljet_td, mdot_norm)

def radio_luminosity_per_freq(Ljet_ADAF, Ljet_td, mdot_norm, mbh, nu):

    #here nu has to be rest-frame in GHz
    lum_1p4GHz_adaf = A_adaf * (mbh / 1e9 * mdot_norm / 0.01)**0.42 * Ljet_ADAF
    lum_1p4GHz_td = A_td * (mbh / 1e9)**0.32 * (mdot_norm / 0.01)**(-1.2) * Ljet_td

    freq_hz = nu * 1e9
    lum_nu = (lum_1p4GHz_adaf + lum_1p4GHz_td) * (nu / 1.4)**(-0.7) / freq_hz #in erg/s/Hz

    return lum_nu

def prepare_data(hdf5_data, seds_nod, seds, lir, index, model_dir, snapshot, filters):


    bin_it = functools.partial(us.wmedians, xbins=xmf)
    total_mags_nod = seds_nod[4]
    total_mags = seds[4]

    Lum_radio_Viperfish = 10**((total_mags[9:16,:] + 48.6)/(-2.5)) * dfac #erg/s/Hz

    ion_mag = total_mags_nod[1,:]
    q_ionis = ionising_photons(ion_mag, 912.0) #in s^-1

    # Unpack data
    (h0, volh, mdisk, mbulge, sfrd, sfrb, idgal, mbh, macc_hh, macc_sb) = hdf5_data

    mbh = mbh/h0
    macc_bh = (macc_hh + macc_sb)/h0/1e9 #in Msun/yr
    (Ljet_ADAF, Ljet_td, mdot_norm) = radio_luminosity_agn(mbh, macc_bh)

    h0log = np.log10(float(h0))

    vol = volh/h0**3

    sfr = sfrd + sfrb

    #select galaxies with Mstar > 0
    ind = np.where(mdisk + mbulge > 0)
    ms = (mdisk[ind] + mbulge[ind])/h0 #in Msun
    sfr = sfr[ind]/h0/1e9 #in Msun/yr
    
    selection_freq = (8.4, 5.0, 3.0, 1.4, 0.61, 0.325, 0.15) #GHz

    lum_radio = np.zeros(shape = (len(selection_freq), len(q_ionis)))
    lum_ratio = np.zeros(shape = (len(selection_freq), len(q_ionis)))
    lum_radio_agn = np.zeros(shape = (len(selection_freq), len(mbh)))

    for i, nu in enumerate(selection_freq):
        lum_radio[i,:] = freefree_lum(q_ionis[:], nu) + synchrotron_lum(sfr[:], nu)
        lum_ratio[i,:] = freefree_lum(q_ionis[:], nu) / lum_radio[i,:]
        lum_radio_agn[i,:] = radio_luminosity_per_freq(Ljet_ADAF[:], Ljet_td[:], mdot_norm[:], mbh[:], nu)

    max_ff = max(lum_ratio[3,:] * lum_radio[3,:])
    sfr_derived = lum_ratio[3,:] * lum_radio[3,:] / (2.17e27) * (1.4)**0.1
    max_sfr_derived = max_ff / (2.17e27) * (1.4)**0.1

    lir_total = lir[1] #total dust luminosity
    qIR_dale14 = np.log10(lir_total[0,:]*Lsunwatts/3.75e12) - np.log10(Lum_radio_Viperfish[3,:]/1e7)
    qIR_bressan = np.log10(lir_total[0,:]*Lsunwatts/3.75e12) - np.log10(lum_radio[3,:]/1e7)

    ind = np.where(lir_total[0,:] > 1e9)
    print(np.median(qIR_dale14[ind]), np.median(qIR_bressan[ind]))

    return (lum_radio/1e7, Lum_radio_Viperfish/1e7, lum_ratio, lum_radio_agn/1e7, ms, sfr, vol, h0)

def plot_comparison_radio_lums(plt, outdir, obsdir, LBressan, LViperfish, Lratio, ms, sfr, filters, redshift):

    bin_it = functools.partial(us.wmedians, xbins=xmf)

    subplots = (331, 332, 333, 334, 335, 336, 337)
    labels = filters

    fig = plt.figure(figsize=(10,10))
    ytit = "$\\rm log_{10} (L_{\\rm Bressan02}/W Hz^{-1})$"
    xtit = "$\\rm log_{10} (L_{\\rm Dale14}/W Hz^{-1})$"
    xmin, xmax, ymin, ymax = 19, 25, 19, 25
    xleg = xmin + 0.15 * (xmax - xmin)
    yleg = ymax - 0.1 * (ymax - ymin)

    for i, subp in enumerate(subplots):
        ax = fig.add_subplot(subp)
        common.prepare_ax(ax, xmin, xmax, ymin, ymax, xtit, ytit, locators=(1, 1, 1, 1))
        ax.text(xleg, yleg, labels[i])
 
        ind = np.where((ms > 1e8) & (sfr > 0) & (LViperfish[i,:] > 1e19))
        x = np.log10(LViperfish[i,ind])
        y = np.log10(LBressan[i,ind])
        meds =  bin_it(x=x[0], y=y[0])
        z = np.log10(Lratio[i,ind])
        im = ax.hexbin(x[0], y[0], z[0], xscale='linear', yscale='linear', gridsize=(20,20), cmap='magma')
        #ax.plot(x[0], y[0], 'ko')
        #cbar_ax = fig.add_axes([0.86, 0.15, 0.025, 0.7])
        cbar = fig.colorbar(im)#, cax=cbar_ax)
        cbar.ax.set_ylabel('$\\rm log_{10}(L_{\\rm ff}/L_{\\rm syn}(Bressan)$)')
        x = [19, 25]
        ax.plot(x, x, linestyle='dotted',color='yellow')

        ind = np.where(meds[0,:] != 0)
        x = xmf[ind]
        y = meds[0,ind] 
        yerrdn = meds[1,ind]
        yerrup = meds[2,ind]
        ax.errorbar(x, y[0], yerr=[yerrdn[0], yerrup[0]], ls='None', mfc='None', ecolor = 'grey', mec='grey',marker='s')
    #common.prepare_legend(ax, cols, loc=3)

    plt.tight_layout()

    common.savefig(outdir, fig, 'radio_lum_comparison_models_z'+redshift+'.pdf')

def plot_radio_lf_z0(plt, output_dir, obs_dir, LBressan, LViperfish, LAGN, redshift, vol, h0):

    fig = plt.figure(figsize=(5,5))
    ytit = "$\\rm log_{10} (\\phi/Mpc^{-3} dex^{-1})$"
    xtit = "$\\rm log_{10} (L_{\\rm 1.4GHz}/W Hz^{-1})$"
    xmin, xmax, ymin, ymax = 19, 25, -7, -1
    xleg = xmin + 0.15 * (xmax - xmin)
    yleg = ymax - 0.1 * (ymax - ymin)
    ax = fig.add_subplot(111)
    common.prepare_ax(ax, xmin, xmax, ymin, ymax, xtit, ytit, locators=(1, 1, 1, 1))

    ind = np.where(LBressan[3,:] > 1e15)
    lum = np.log10(LBressan[3,ind])
    lum = lum[0]
    print(lum) 
    HBress, _ = np.histogram(lum,bins=np.append(mbins,mupp))
    print(HBress, vol)
    lum = np.log10(LViperfish[3,ind])
    lum = lum[0] 
    HDale, _ = np.histogram(lum,bins=np.append(mbins,mupp))
    HBress = HBress/vol/dm
    HDale = HDale/vol/dm
  
    ind = np.where(HBress[:] != 0)
    print(np.log10(HBress[ind]))
    ax.plot(xmf[ind], np.log10(HBress[ind]), linestyle='solid', color='red', label='Obi+17')
    ind = np.where(HDale[:] != 0)
    ax.plot(xmf[ind], np.log10(HDale[ind]), linestyle='dashed', color='blue', label='Dale+14')

    lm, p, dpup, dpdn = common.load_observation(obs_dir, 'lf/lf1p4GHz_z0_mauch07.data', [0,1,2,3])
    hobs = 0.7
    xobs = lm + 2.0 * np.log10(hobs/h0)
    yobs = p - 3.0 * np.log10(hobs/h0) + 0.4 #the 0.4 comes from the normalisation required to go from mag^-1 to dex^-1 (from Bonato et al. 2017)
    ax.errorbar(xobs, yobs, yerr=[dpdn, dpup], ls='None', mfc='None', ecolor = 'grey', mec='grey',marker='o',label="Mauch & Sadler (2007)")
 
    common.prepare_legend(ax, ['red', 'blue', 'grey'], loc=3)
    plt.tight_layout()
    common.savefig(output_dir, fig, 'radio_lum_function_z'+redshift+'.pdf')


    fig = plt.figure(figsize=(5,5))
    ytit = "$\\rm log_{10} (\\phi/Mpc^{-3} dex^{-1})$"
    xtit = "$\\rm log_{10} (L_{\\rm 1.4GHz}/W Hz^{-1})$"
    xmin, xmax, ymin, ymax = 19, 27, -7, -1
    xleg = xmin + 0.15 * (xmax - xmin)
    yleg = ymax - 0.1 * (ymax - ymin)
    ax = fig.add_subplot(111)
    common.prepare_ax(ax, xmin, xmax, ymin, ymax, xtit, ytit, locators=(1, 1, 1, 1))

    ind = np.where(LAGN[3,:] > 1e15)
    lum = np.log10(LAGN[3,ind])
    lum = lum[0]
    HAGN, _ = np.histogram(lum,bins=np.append(mbins,mupp))
    HAGN = HAGN/vol/dm
  
    ind = np.where(HAGN[:] != 0)
    ax.plot(xmf[ind], np.log10(HAGN[ind]), linestyle='solid', color='red', label='Griffin+19')

    lm, p, dpup, dpdn = common.load_observation(obs_dir, 'lf/lf1p4GHz_agn_z0_mauch07.data', [0,1,2,3])
    hobs = 0.7
    xobs = lm + 2.0 * np.log10(hobs/h0)
    yobs = p - 3.0 * np.log10(hobs/h0) + 0.4 #the 0.4 comes from the normalisation required to go from mag^-1 to dex^-1 (from Bonato et al. 2017)
    ax.errorbar(xobs, yobs, yerr=[dpdn, dpup], ls='None', mfc='None', ecolor = 'grey', mec='grey',marker='o',label="Mauch & Sadler (2007)")
 
    common.prepare_legend(ax, ['red', 'grey'], loc=3)
    plt.tight_layout()
    common.savefig(output_dir, fig, 'radio_lum_function_AGN_z'+redshift+'.pdf')


def main(model_dir, output_dir, redshift_table, subvols, obs_dir):

    #zlist = np.arange(2,10,0.25)
    #zlist = (0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 0, 0.25, 0.5, 1, 2, 3, 4, 6, 8, 9, 10)
    filters = ('8.4GHz', '5GHz', '3GHz', '1.4GHz', '610MHz', '325MHz', '150MHz') 

    file_hdf5_sed = "Shark-SED-eagle-rr14-radio-only.hdf5"
    #(0): "z_SDSS", "Band_ionising_photons", "FUV_Nathan", "Band9_ALMA",
    #(4): "Band8_ALMA", "Band7_ALMA", "Band6_ALMA", "Band4_ALMA", "Band3_ALMA",
    #(9): "BandX_VLA", "BandC_VLA", "BandS_VLA", "BandL_VLA", "Band_610MHz",
    #(14): "Band_325MHz", "Band_150MHz"

    #199 188 159 131 113 100 88 79 70 63 57 51
    zlist = [0, 0.194738848008908, 0.909822023685613, 2.00391410007239, 3.0191633709527, 3.95972701662501, 5.02220991014863, 5.96592270612165, 7.05756323172746, 8.0235605165086, 8.94312532315157, 9.95650268434316] #9.95655] #0.194739, 0.254144, 0.359789, 0.450678, 0.8, 0.849027, 0.9, 1.20911, 1.28174, 1.39519, 1.59696, 2.00392, 2.47464723643932, 2.76734390952347, 3.01916, 3.21899984389701, 3.50099697082904, 3.7248038025221, 3.95972, 4.465197621546, 4.73693842543988] #[5.02220991014863, 5.52950356184419, 5.96593, 6.55269895697227, 7.05756323172746, 7.45816170313544, 8.02352, 8.94312532315157, 9.95655]
    #[0.016306640039433, 0.066839636933135, 0.084236502339783, 0.119886040396529, 0.138147164704691, 0.175568857770275, 0.214221447279112, 0.23402097095238, 0.274594901875312, 0.316503156974571]

    znames = ['0', '0p2', '0p9', '2', '3', '4', '5', '6', '7', '8', '9', '10']
    plt = common.load_matplotlib()
    fields = {'galaxies': ('mstars_disk', 'mstars_bulge','sfr_disk','sfr_burst','id_galaxy',
                           'm_bh', 'bh_accretion_rate_hh', 'bh_accretion_rate_sb')}
   
    fields_sed_nod = {'SED/ab_nodust': ('bulge_d','bulge_m','bulge_t','disk','total')}
    fields_sed = {'SED/ab_dust': ('bulge_d','bulge_m','bulge_t','disk','total')}
    fields_lir = {'SED/lir_dust': ('disk','total'),}

    for index, snapshot in enumerate(redshift_table[zlist]):
        hdf5_data = common.read_data(model_dir, snapshot, fields, subvols)
        seds_nod = common.read_photometry_data_variable_tau_screen(model_dir, snapshot, fields_sed_nod, subvols, file_hdf5_sed)
        seds = common.read_photometry_data_variable_tau_screen(model_dir, snapshot, fields_sed, subvols, file_hdf5_sed)
        lir = common.read_photometry_data_variable_tau_screen(model_dir, snapshot, fields_lir, subvols, file_hdf5_sed)
        (LBressan, LViperfish, Lratio, LAGN, ms, sfr, vol, h0) = prepare_data(hdf5_data, seds_nod, seds, lir, index, model_dir, snapshot, filters)
        plot_comparison_radio_lums(plt, output_dir, obs_dir, LBressan, LViperfish, Lratio, ms, sfr, filters, znames[index])
        if(snapshot == 199):
           plot_radio_lf_z0(plt, output_dir, obs_dir, LBressan, LViperfish, LAGN, znames[index], vol, h0)

if __name__ == '__main__':
    main(*common.parse_args())