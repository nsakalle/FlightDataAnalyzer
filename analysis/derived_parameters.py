import logging
import numpy as np

from analysis.node import A, DerivedParameterNode, KPV, KTI, P, S, Parameter

from analysis.library import (align, 
                              first_order_lag,
                              first_order_washout,
                              hysteresis, 
                              interleave,
                              rate_of_change, 
                              repair_mask,
                              straighten_headings,
                              vstack_params)

from settings import (AZ_WASHOUT_TC,
                      HYSTERESIS_FPALT,
                      HYSTERESIS_FPALT_CCD,
                      HYSTERESIS_FP_RAD_ALT,
                      HYSTERESIS_FPIAS, 
                      HYSTERESIS_FPROC,
                      GRAVITY,
                      KTS_TO_FPS,
                      RATE_OF_CLIMB_LAG_TC
                      )

#-------------------------------------------------------------------------------
# Derived Parameters


# Q: What do we do about accessing KTIs - params['a kti class name'] is a list of kti's
#   - could have a helper - filter_for('kti_name', take_max=True) # and possibly take_first, take_min, take_last??

# Q: Accessing information like ORIGIN / DESTINATION

# Q: What about V2 Vref etc?


class AccelerationVertical(DerivedParameterNode):
    def derive(self, acc_norm=P('Acceleration Normal'), 
               acc_lat=P('Acceleration Lateral'), 
               acc_long=P('Acceleration Longitudinal'), 
               pitch=P('Pitch'), roll=P('Roll')):
        """
        Resolution of three accelerations to compute the vertical
        acceleration (perpendicular to the earth surface).
        """
        # Align the acceleration and attitude samples to the normal acceleration,
        # ready for combining them.
        # "align" returns an array of the first parameter aligned to the second.
        ax = align(acc_long, acc_norm) 
        pch = np.radians(align(pitch, acc_norm))
        ay = align(acc_lat, acc_norm) 
        rol = np.radians(align(roll, acc_norm))
        
        # Simple Numpy algorithm working on masked arrays
        resolved_in_pitch = ax * np.sin(pch) + acc_norm.array * np.cos(pch)
        self.array = resolved_in_pitch * np.cos(rol) - ay * np.sin(rol)


class AccelerationForwardsForFlightPhases(DerivedParameterNode):
    # List the minimum acceptable parameters here
    @classmethod
    def can_operate(cls, available):
        if 'Airspeed' in available:
            return True
        elif 'Acceleration Longitudinal' in available:
            return True
        else:
            return False
        
    # List the optimal parameter set here
    def derive(self, acc_long=P('Acceleration Longitudinal'),
               airspeed=P('Airspeed')):
        """
        Acceleration or deceleration on the runway is used to identify the
        runway heading. For the Hercules aircraft there is no longitudinal
        accelerometer, so rate of change of airspeed is used instead.
        """
        if acc_long:
            self.array = repair_mask(acc_long.array)
        else:
            aspd = P('Aspd',array=repair_mask(airspeed.array))
            roc_aspd = rate_of_change(aspd, 1) * KTS_TO_FPS/GRAVITY
            self.array =  roc_aspd 


class AirspeedForFlightPhases(DerivedParameterNode):
    def derive(self, airspeed=P('Airspeed')):
        self.array = hysteresis(airspeed.array, HYSTERESIS_FPIAS)


class AccelerationFromAirspeed(DerivedParameterNode):
    """
    This paraeter is included for the few aircraft that do not have a
    longitudinal accelerometer installed, so we can identify acceleration or
    deceleration on the runway.
    """
    def derive(self, airspeed=P('Airspeed')):
        self.array = rate_of_change(airspeed, 1)


class AirspeedMinusVref(DerivedParameterNode):
    def derive(self, airspeed=P('Airspeed'), vref=P('Vref')):
        vref_aligned = align(vref, airspeed)
        self.array = airspeed.array - vref_aligned


class AirspeedTrue(DerivedParameterNode):
    #dependencies = ['SAT', 'VMO', 'MMO', 'Indicated Airspeed', 'Altitude QNH']
    # TODO: Move required dependencies from old format above to derive kwargs.
    def derive(self, ias = P('Airspeed'),
               alt_std = P('Altitude STD'),
               sat = P('SAT')):
        return NotImplemented
    

class AltitudeAAL(DerivedParameterNode):
    name = 'Altitude AAL'
    def derive(self, alt_std=P('Altitude STD'), alt_rad=P('Altitude Radio')):
        return NotImplemented

    
class AltitudeAALForFlightPhases(DerivedParameterNode):
    name = 'Altitude AAL For Flight Phases'
    # This crude parameter is used for flight phase determination,
    # and only uses airspeed and pressure altitude for robustness.
    def derive(self, alt_std=P('Altitude STD'), fast=P('Fast')):
        
        # Initialise the array to zero, so that the altitude above the airfield
        # will be 0ft when the aircraft cannot be airborne.
        self.array = np.ma.zeros(len(alt_std.array))
        
        repair_mask(alt_std.array) # Remove small sections of corrupt data

        for speedy in fast:
            begin = speedy.slice.start
            end = speedy.slice.stop
            peak = np.ma.argmax(alt_std.array[speedy.slice])
            self.array[begin:begin+peak] = alt_std.array[begin:begin+peak] - alt_std.array[begin]
            self.array[begin+peak:end] = alt_std.array[begin+peak:end] - alt_std.array[end]
    
    
class AltitudeForClimbCruiseDescent(DerivedParameterNode):
    name = 'Altitude For Climb Cruise Descent'
    def derive(self, alt_std=P('Altitude STD')):
        self.array = hysteresis ( alt_std.array, HYSTERESIS_FPALT_CCD)
    
    
class AltitudeForFlightPhases(DerivedParameterNode):
    def derive(self, alt_std=P('Altitude STD')):
        self.array = hysteresis (repair_mask(alt_std.array), HYSTERESIS_FPALT)
    
    
class AltitudeRadio(DerivedParameterNode):
    # This function allows for the distance between the radio altimeter antenna
    # and the main wheels of the undercarriage.

    # The parameter raa_to_gear is measured in feet and is positive if the
    # antenna is forward of the mainwheels.
    def derive(self, alt_rad=P('Altitude Radio Sensor'), pitch=P('Pitch'),
               main_gear_to_alt_rad=A('Main Gear To Altitude Radio')):
        # Align the pitch attitude samples to the Radio Altimeter samples,
        # ready for combining them.
        pitch_aligned = np.radians(align(pitch, alt_rad))
        # Now apply the offset if one has been provided
        self.array = alt_rad.array - np.sin(pitch_aligned) * main_gear_to_alt_rad.value


class AltitudeRadioForFlightPhases(DerivedParameterNode):
    def derive(self, alt_rad=P('Altitude Radio')):
        self.array = hysteresis (repair_mask(alt_rad.array), HYSTERESIS_FP_RAD_ALT)


class AltitudeQNH(DerivedParameterNode):
    name = 'Altitude QNH'
    def derive(self, param=P('Flap')):
        return NotImplemented


class AltitudeTail(DerivedParameterNode):
    # This function allows for the distance between the radio altimeter antenna
    # and the point of the airframe closest to tailscrape.
    
    # The parameter gear_to_tail is measured in feet and is the distance from 
    # the main gear to the point on the tail most likely to scrape the runway.
    def derive(self, alt_rad = P('Altitude Radio'), 
               pitch = P('Pitch'),
               dist_gear_to_tail=A('Dist Gear To Tail')):
        
        # Align the pitch attitude samples to the Radio Altimeter samples,
        # ready for combining them.
        pitch_aligned = np.radians(align(pitch, alt_rad))
        # Now apply the offset
        self.array = alt_rad.array - np.sin(pitch_aligned) * dist_gear_to_tail.value
        

class ClimbForFlightPhases(DerivedParameterNode):
    def derive(self, alt_std=P('Altitude STD'), airs=P('Fast')):
        self.array = np.ma.zeros(len(alt_std.array))
        repair_mask(alt_std.array) # Remove small sections of corrupt data
        for air in airs:
            ax = air.slice
            # Initialise the tracking altitude value
            curr_alt = alt_std.array[ax][0]
            self.array[ax][0] = 0.0
            for count in range(1, ax.stop - ax.start):
                if alt_std.array[ax][count] < alt_std.array[ax][count-1]:
                    # Going down, keep track of current altitude
                    curr_alt = alt_std.array[ax][count]
                    self.array[ax][count] = 0.0
                else:
                    self.array[ax][count] = alt_std.array[ax][count] - curr_alt
    

class DistanceToLanding(DerivedParameterNode):
    def derive(self, alt_aal = P('Altitude AAL'),
               gspd = P('Ground Speed'),
               ils_gs = P('Glideslope Deviation'),
               ldg = P('LandingAirport')):
        return NotImplemented
    

class EngN1Average(DerivedParameterNode):
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class EngN1Minimum(DerivedParameterNode):
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
    
    def derive(self, 
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class EngN2Average(DerivedParameterNode):
    @classmethod
    def can_operate(cls, available):
        # works with any combination of params available
        if any([d in available for d in cls.get_dependency_names()]):
            return True
        
    def derive(self, 
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class FlapCorrected(DerivedParameterNode):
    def derive(self, flap=P('Flap')):
        return NotImplemented
    

class GearSelectedDown(DerivedParameterNode):
    # And here is where the nightmare starts.
    # Sometimes recorded
    # Sometimes interpreted from other signals
    # There's no pattern to how this is worked out.
    # For aircraft with a Gear Selected Down parameter let's try this...
    def derive(self, param=P('Gear Selected Down FDR')):
        return NotImplemented


class GearSelectedUp(DerivedParameterNode):
    def derive(self, param=P('Gear Selected Up FDR')):
        pass


class HeadingContinuous(DerivedParameterNode):
    """
    For all internal computing purposes we use this parameter which does not
    jump as it passes through North. To recover the compass display, modulus
    ("%360" in Python) returns the value to display to the user.
    """
    def derive(self, head_mag=P('Heading Magnetic')):
        self.array = repair_mask(straighten_headings(head_mag.array))


class HeadingTrue(DerivedParameterNode):
    # Requires the computation of a magnetic deviation parameter linearly 
    # changing from the deviation at the origin to the destination.
    def derive(self, head = P('Heading Continuous'),
               dev = P('Magnetic Deviation')):
        dev_array = align(dev, head)
        self.array = head + dev_array
    

class ILSLocalizerGap(DerivedParameterNode):
    def derive(self, ils_loc = P('Localizer Deviation'),
               alt_aal = P('Altitude AAL')):
        return NotImplemented

    
class ILSGlideslopeGap(DerivedParameterNode):
    def derive(self, ils_gs = P('Glideslope Deviation'),
               alt_aal = P('Altitude AAL')):
        return NotImplemented
 
    
class MACH(DerivedParameterNode):
    def derive(self, ias = P('Airspeed'), tat = P('TAT'),
               alt = P('Altitude Std')):
        return NotImplemented
        

class RateOfClimb(DerivedParameterNode):
    """
    This routine derives the rate of climb from the vertical acceleration, the
    Pressure altitude and the Radio altitude.
    
    We use pressure altitude rate above 100ft and radio altitude rate below
    50ft, with a progressive changeover across that range. Below 100ft the
    pressure altitude information is affected by the flow field around the
    aircraft, while above 50ft there is an increasing risk of changes in
    ground profile affecting the radio altimeter signal.
    
    Complementary first order filters are used to combine the acceleration
    data and the height data. A high pass filter on the altitude data and a
    low pass filter on the acceleration data combine to form a consolidated
    signal.
    
    By merging the altitude rate signals, we avoid problems of altimeter
    datums affecting the transition as these will have been washed out by the
    filter stage first.
    
    Long term errors in the accelerometers are removed by washing out the
    acceleration term with a longer time constant filter before use. The
    consequence of this is that long period movements with continued
    acceleration will be underscaled slightly. As an example the test case
    with a 1ft/sec^2 acceleration results in an increasing rate of climb of
    55 fpm/sec, not 60 as would be theoretically predicted.
    """
    # List the minimum acceptable parameters here
    @classmethod
    def can_operate(cls, available):
        # List the minimum required parameters. If 'Altitude Radio For Flight
        # Phases' is available, that's a bonus and we will use it, but it is
        # not required.
        if 'Altitude STD' in available:
            return True
        else:
            return False
    
    def derive(self, 
               az = P('Acceleration Vertical'),
               alt_std = P('Altitude STD'),
               alt_rad = P('Altitude Radio')):
        if az and alt_rad:
            # Use the complementary smoothing approach

            roc_alt_std = first_order_washout(alt_std.array,
                                              RATE_OF_CLIMB_LAG_TC, az.hz)
            roc_alt_rad = first_order_washout(alt_rad.array,
                                              RATE_OF_CLIMB_LAG_TC, az.hz)
                    
            # Use pressure altitude rate above 100ft and radio altitude rate
            # below 50ft with progressive changeover across that range.
            # up to 50 ft radio 0 < std_rad_ratio < 1 over 100ft radio
            std_rad_ratio = np.maximum(np.minimum(
                (alt_rad.array.data-50.0)/50.0,
                1),0)
            roc_altitude = roc_alt_std*std_rad_ratio +\
                roc_alt_rad*(1.0-std_rad_ratio)
                
            roc_altitude /= RATE_OF_CLIMB_LAG_TC # Remove washout gain  
            
            # Lag this rate of climb
            az_washout = first_order_washout (az.array, AZ_WASHOUT_TC, az.hz, initial_value = az.array[0])
            inertial_roc = first_order_lag (az_washout, RATE_OF_CLIMB_LAG_TC, az.hz, gain=GRAVITY*RATE_OF_CLIMB_LAG_TC)
            self.array = (roc_altitude + inertial_roc) * 60.0
        else:
            self.array = rate_of_change(alt_std,2)*60


class RateOfClimbForFlightPhases(DerivedParameterNode):
    def derive(self, alt_std = P('Altitude STD')):
        self.array = rate_of_change(repair_mask(alt_std),2)*60


class Relief(DerivedParameterNode):
    # also known as Terrain
    
    # Quickly written without tests as I'm really editing out the old dependencies statements :-(
    def derive(self, alt_aal = P('Altitude AAL'),
               alt_rad = P('Radio Altitude')):
        altitude = align(alt_aal, alt_rad)
        self.array = altitude - alt_rad


class Speedbrake(DerivedParameterNode):
    def derive(self, param=P('Speedbrake FDR')):
        # There will be a recorded parameter, but varying types of correction will 
        # need to be applied according to the aircraft type and data frame.
        self.array = param


'''

Better done together

class SmoothedLatitude(DerivedParameterNode): # TODO: Old dependency format.
    dependencies = ['Latitude', 'True Heading', 'Indicated Airspeed'] ##, 'Altitude Std']
    def derive(self, params):
        return NotImplemented
    
class SmoothedLongitude(DerivedParameterNode): # TODO: Old dependency format.
    dependencies = ['Longitude', 'True Heading', 'Indicated Airspeed'] ##, 'Altitude Std']
    def derive(self, params):
        return NotImplemented
'''


class RateOfTurn(DerivedParameterNode):
    def derive(self, head = P('Heading Continuous')):
        self.array = rate_of_change(head, 1)


class Pitch(DerivedParameterNode):
    name = "Pitch"
    def derive(self, p1=P('Pitch (1)'), p2=P('Pitch (2)')):
        self.hz = p1.hz * 2
        self.offset = min(p1.offset, p2.offset)
        self.array = interleave (p1, p2)


'''
############  TODO: NEED TO WORK OUT how to handle multiple engines. ###########
'''

class EngEGT(DerivedParameterNode):
    name = "Eng EGT"
    def derive(self, 
               param1=P('Eng (1) EGT'),
               param2=P('Eng (2) EGT'),
               param3=P('Eng (3) EGT'),
               param4=P('Eng (4) EGT')):
        return NotImplemented


class EngN1(DerivedParameterNode):
    def derive(self, 
               param1=P('Eng (1) N1'),
               param2=P('Eng (2) N1'),
               param3=P('Eng (3) N1'),
               param4=P('Eng (4) N1')):
        return NotImplemented


class EngN2(DerivedParameterNode):
    def derive(self, 
               param1=P('Eng (1) N2'),
               param2=P('Eng (2) N2'),
               param3=P('Eng (3) N2'),
               param4=P('Eng (4) N2')):
        return NotImplemented


class EngOilPress(DerivedParameterNode):
    name = "Eng Oil Press"
    def derive(self, 
               param1=P('Eng (1) Oil Press'),
               param2=P('Eng (2) Oil Press'),
               param3=P('Eng (3) Oil Press'),
               param4=P('Eng (4) Oil Press')):
        return NotImplemented


class EngOilPressLow(DerivedParameterNode):
    name = 'Eng Oil Press Low'
    def derive(self, 
               param1=P('Eng (1) Oil Press Low'),
               param2=P('Eng (2) Oil Press Low'),
               param3=P('Eng (3) Oil Press Low'),
               param4=P('Eng (4) Oil Press Low')):
        return NotImplemented


class EngAverage(DerivedParameterNode): # Q: is this a parameter?
    def derive(self, param=P('Flap')): # Q: Args?
        return NotImplemented


class ThrustLever(DerivedParameterNode):
    name = 'Thrust Lever'
    def derive(self, param=P('Flap')): # Q: Args?
        return NotImplemented


class EngVibN2(DerivedParameterNode):
    name = 'Eng Vib N2'
    def derive(self, param=P('Flap')): # Q: Args?
        return NotImplemented



'''
########## FLIGHT PHASES ###########

class GoAround(DerivedParameterNode): # Q: is this a parameter?
    def derive(self, param=P('Flap')): # Q: Args?
        return NotImplemented


class RudderReversal(DerivedParameterNode):
    def derive(self, param=P('Flap')): # Q: Args?
        return NotImplemented

'''

'''
########## RECORDED ###########


class AccelerationLateral(DerivedParameterNode):
    def derive(self, param=P('Acceleration Lateral')):
        return NotImplemented


class GPWSDontSink(DerivedParameterNode):
    name = 'GPWS Don't Sink'
    def derive(self, param=P('GPWS Dont Sink Warning')):
    
    
class Eng1OilTemp(DerivedParameterNode):
    name = 'Eng (1) Oil Temp'
    def derive(self, param=P('Eng (1) Oil Temp')):
        return NotImplemented


class GPWSSinkRate(DerivedParameterNode):
    name = "GPWS Sink Rate"
    def derive(self, param=P('Flap')):
        return NotImplemented


class GPWSGlideslope(DerivedParameterNode):
    name = "GPWS Glideslope"
    def derive(self, param=P('Flap')):
        return NotImplemented


class GPWSWindshear(DerivedParameterNode):
    name = "GPWS Windshear"
    def derive(self, param=P('Flap')):
        return NotImplemented


class GPWSTooLowFlap(DerivedParameterNode):
    name = 'GPWS Too Low Flap'
    def derive(self, param=P('Flap')):
        return NotImplemented


class GPWSTooLowGear(DerivedParameterNode):
    name = 'GPWS Too Low Gear'
    def derive(self, param=P('Flap')):
        return NotImplemented


# Are the following the same?
class GPWSTooLowTerrain(DerivedParameterNode):
    name = 'GPWS Too Low Terrain'
    def derive(self, param=P('Flap')):
        return NotImplemented


class GPWSTerrainPullUp(DerivedParameterNode):
    name = 'GPWS Terrain Pull Up'
    def derive(self, param=P('Flap')):
        return NotImplemented


class GPWSTerrain(DerivedParameterNode):
    name = 'GPWS Terrain'
    def derive(self, param=P('Flap')):
        return NotImplemented
    

class ILSLocalizer(DerivedParameterNode):
    name = "ILS Localizer"
    def derive(self, param=P('Flap')):
        return NotImplemented


class GrossWeight(DerivedParameterNode):
    def derive(self, param=P('Flap')): # Q: Args?
        return NotImplemented
'''
