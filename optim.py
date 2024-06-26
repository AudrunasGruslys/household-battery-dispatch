import pulp
import numpy as np


class EnergyOptimizer():

    def __init__(self, n_hours):
        """
        Home energy optimizer that plans hourly usage of various electricity consumers and produceds
        for n_hours ahead.
        """
        self.problem = pulp.LpProblem("Power_Optimization", pulp.LpMinimize)
        self.n_hours = n_hours
        self.hours = range(n_hours)
        # Total cost of the period of modelling. This variable gets updated every time when
        # consumers and producers get added to the system. This is the variable that gets
        # minimized by the linear solver
        self.total_cost = 0.0
        # Variables tracking energy balance as difference devices are added. Must sum to zero.
        self.energy_balance = [0.0 for hour in self.hours]
        # Nested dict containing all other variables.
        self.vars = {}    

    def _add_cost(self, cost):
        self.total_cost = self.total_cost + cost        

    def _add_to_energy_balance(self, hour, energy):
        """
        Updates energy balance for a given hour. All hourly balances are constraint to sum to zero.
        """
        self.energy_balance[hour] = self.energy_balance[hour] + energy   
        print(hour, self.energy_balance[hour])

    def _new_time_series(self, device_name: str, var_name: str, lowBound=None, upBound=None, binary=False):
        """
        Create a new time series.
        """
        if binary:
            assert lowBound == None
            assert upBound == None
            var = pulp.LpVariable.dicts(device_name + "_" + var_name, self.hours, lowBound=0, upBound=1, cat='Integer')
        else:
            var = pulp.LpVariable.dicts(device_name + "_" + var_name, self.hours, lowBound=lowBound, upBound=upBound)

        if not device_name in self.vars:
            self.vars[device_name] = {}
        device_vars = self.vars[device_name]
        assert not var_name in device_vars
        device_vars[var_name] = var
        return var

    def solve(self):
        """
        Solves the system of equations. A solution may or may not exist.
        Must be called after all devices are added.
        """
        for hour in self.hours:
            self.problem += self.energy_balance[hour] == 0
        self.problem += self.total_cost    
        status = self.problem.solve()

    def get_time_series(self):
        """
        Returns a nested dictionary containing all time series as np ndarray.
        """
        res = {}
        for device_name in self.vars:
            device_vars = self.vars[device_name]
            device_series = {}
            for var_name in device_vars:
                var = device_vars[var_name]
                device_series[var_name] = np.array([var[hour].varValue for hour in self.hours], dtype=np.float32)
            res[device_name] = device_series
        return res

    def print_time_series(self, ts=None, prefix=''):
        if ts is None:
            ts = self.get_time_series()
        for key, value in ts.items():
            if type(value) is dict:
                print(prefix + key + ':')
                self.print_time_series(value, prefix + '    ')
            else:
                print(prefix + key + ':', value)

    def add_mains_electricity_supply(self, name, max_import_power, import_hourly_prices, max_export_power=0, export_hourly_prices=None):
        """
        Add mains electricy supply the system. Optimizer can work with several different
        electricity supplies (for instance, mains supply and a diesel generator with estimated running costs as hourly_prices)
        Args:
          max_power: max power provided by the mains supply.
          hourly_prices: hourly electricity prices
          max_export_power: maximum export power provided by the mains supply
          export_hourly_prices: either known export prices (if net billing is used) or estimated value of electricity credits 
            if net metering is used; the later would most likely be waverage winter electricity tariffs after subtracting any 
            'grid storage' fees.
        """
        assert max_import_power >= 0
        assert max_export_power >= 0
        assert len(import_hourly_prices) == self.n_hours
        # dfirection: 0 for exports, 1 for imports.
        direction = self._new_time_series(name, "direction", binary=True)
        electricity_import = self._new_time_series(name, "import", lowBound=0, upBound=max_import_power)
        electricity_export = self._new_time_series(name, "export", lowBound=-max_export_power, upBound=0)
        electricity_import_cost = pulp.lpSum(electricity_import[hour] * import_hourly_prices[hour] for hour in self.hours)
        self._add_cost(electricity_import_cost)
        if max_export_power > 0:
            assert len(export_hourly_prices) == self.n_hours
            electricity_export_cost = pulp.lpSum(electricity_export[hour] * export_hourly_prices[hour] for hour in self.hours)
            # the later cost is negative, so it is actually a profit.
            self._add_cost(electricity_export_cost)
        for hour in self.hours:
            self._add_to_energy_balance(hour, electricity_import[hour] + electricity_export[hour])
            # Make sure that we either import or export, but do not do both at the same time.
            self.problem += electricity_import[hour] <= max_import_power * direction[hour]
            self.problem += electricity_export[hour] >= -max_export_power * (1 - direction[hour])
        
        return electricity_import

    def _get_hourly(self, x, hour):
        if isinstance(x, int):
            return x
        if isinstance(x, float):
            return x
        assert len(x) == self.n_hours    
        return x[hour]    

    def add_battery(self, name, capacity, initial_soc, efficiency,
            max_charge_power, max_discharge_power, cost_of_cycle_kwh,
            final_energy_value_per_kwh, min_soc=None, max_soc=None):  
        """
        Add battery into the system. Assumied minimal state of charge is 0 - if one wants to maintain some other
        minimal state of charge one has to model a smaller battery and shift all SOC values by the desired amount.
        Args:
          capacity: battery capacity in kwh
          initial_soc: initial state of charge (scalar)
          efficiency: round trip efficiency
          max_charge_power: maximum cgarging power
          max_discharge_power: maximum discharge power
          cost_of_cycle_kwh: estimated battery round trip amortization cost.
          final_energy_value_per_kwh: estimated final value of energy after the last known hour;
            without it the controller would fully discharge the battery.
          min_soc: minimum final soc at the end each hour (scalar or time series)
          max_soc: maximum final soc at the end of each hour (scalar or time series)
        """  
        assert max_charge_power > 0
        assert max_discharge_power > 0
        charge_rate = self._new_time_series(name, "charge_rate", lowBound = 0, upBound= max_charge_power)
        discharge_rate = self._new_time_series(name, "discharge_rate", lowBound = -max_discharge_power, upBound=0)
        soc = self._new_time_series(name, "soc", lowBound=0, upBound=capacity)
        current_soc = initial_soc
        for hour in self.hours:
            self._add_to_energy_balance(hour, -charge_rate[hour] - discharge_rate[hour] * efficiency)
            self.problem += soc[hour] == current_soc + charge_rate[hour] + discharge_rate[hour] # Conservation of charge 
            current_soc = soc[hour]
            s0, s1 = None, None
            if not min_soc is None:
                s0 = self._get_hourly(min_soc, hour)
                self.problem += current_soc >= s0
            if not max_soc is None:
                s1 = self._get_hourly(max_soc, hour)
                self.problem += current_soc <= s1
            if not s0 is None and not s1 is None:
                assert s0 <= s1    
        amortization_cost = pulp.lpSum(charge_rate[hour] * cost_of_cycle_kwh for hour in self.hours)    
        remaining_value = current_soc * efficiency * final_energy_value_per_kwh
        self._add_cost(amortization_cost - remaining_value)
        return soc, charge_rate, discharge_rate

    def add_fixed_consumption(self, name, hourly_consumption):  
        """
        Add simple fixed hourly consumption that can not be optimized.
        """
        assert len(hourly_consumption) == self.n_hours
        consumption = self._new_time_series(name, "consumption", lowBound = 0, upBound= np.amax(hourly_consumption))
        for hour in self.hours:
            assert hourly_consumption[hour] >= 0 
            self._add_to_energy_balance(hour, -consumption[hour])
            self.problem += consumption[hour] == hourly_consumption[hour]
        return consumption

    def add_solar_production(self, name, estimated_hourly_production):  
        """
        A solar plant with an extimated_hourly production.
        Actual production may be lower if the system does not find a way to export or consume available energy. 
        """
        assert len(estimated_hourly_production) == self.n_hours
        production = self._new_time_series(name, "production", lowBound = 0, upBound= np.amax(estimated_hourly_production))
        for hour in self.hours:
            self.problem += production[hour] >= 0
            self.problem += production[hour] <= estimated_hourly_production[hour]
            self._add_to_energy_balance(hour, production[hour])
        return production

    def add_flexible_consumption(self, name, max_power, min_cumulative_consuption):  
        """
        Adds a total cumulative consumption demand when it is not important when exactly it happens as long as it happens by some hour.
        A good usecase is charging electric cars.
        max_power:
          maximum cunsuption power (would be max charging power in case of an EV)
        min_cumulative_consuption:
          energy consumption (e.g. electric car charging) that has to happen on or before a set hour. For instance, if the only
          demand is to have a car charged by 10kWh in 5 hours time, one can add min_cumulative_consuption=[0,0,0,0,10] requirement.
          However, one can also demant that half would be charhed in 3 hours time (because of, say, potential emergencies)
          and another half has to be charged in 5 hours time: [0,0,5,0,10]. Depending on pricing and other constraints
          (such as max_power constraint) the algorithm may chose to do this earlier, e.g. may distribute charging as [2,2,1,5,0]
        """  
        assert len(min_cumulative_consuption) == self.n_hours
        consumption = self._new_time_series(name, "consumption", lowBound = 0, upBound=max_power)
        cumul_consumption = 0.0
        for hour in self.hours:
            assert min_cumulative_consuption[hour] >= 0 
            self._add_to_energy_balance(hour, -consumption[hour])
            cumul_consumption = cumul_consumption + consumption[hour]
            self.problem += cumul_consumption >= min_cumulative_consuption[hour]
        return consumption    
    
    def add_heating_consumption(self, name, max_heat_power, hourly_demand, tol_cumul_min, tol_cumul_max, final_energy_value_per_kwh):  
        """
        Models a heat pump consumption that may be throttled up or down within desired bounds.
        We are operating in electricity kWh and not heat kWh (that would be further multipled by COP)
        hourly_demand must be expressed in electricity kWh
        Arguments:
           name: name of the device
           hourly_demeand: estimated hourly heat pump demand at the desired steady state temperature.
              It should be calculated using weather forecast data and known tabulated heating power values
              for a given house at a steady state
           tol_cumul_min/max: minimal and maximal deviations of planned cumulative heating power from its target;
              as house temperature change is proportional to the cumulative heating input minus external cooling loss (the later is fixed),
              house temperature tolerance can be expressed in terms of cumulative heating energy toleance which can
              be estimated from known temperature-gradient vs heating energy table (outside of the scope of this function) 
              Cumulative power deviations from hourly_demand is proportional to temperature drop or rise inside the house.
              If the house is already too cold/hot,
              the appropriate min/max value should be set to 0. tol_cumul_min <= 0; tol_cumul_max >= 0
              If tolerance is zero on both sides then the system has no space for heating optimizations.
           final_energy_value_per_kwh: estimated electricity price after the last hour with known price; depending on this
              value the planner may plan to leave the house slighly warmer or cooler.   
        """  
        assert tol_cumul_min <= 0
        assert tol_cumul_max >= 0
        assert len(hourly_demand) == self.n_hours
        heating_power = self._new_time_series(name, "consumption", lowBound = 0, upBound=max_heat_power)
        cumul_demand = 0.0
        cumul_power= 0.0
        for hour in self.hours:
            assert hourly_demand[hour] >= 0 
            cumul_demand = cumul_demand + hourly_demand[hour]
            cumul_power = cumul_power + heating_power[hour]
            self._add_to_energy_balance(hour, -heating_power[hour])
            self.problem += cumul_power >= cumul_demand + tol_cumul_min
            self.problem += cumul_power <= cumul_demand + tol_cumul_max
        # Reward for accumulating heat and penalize for final underheating.    
        self._add_cost((cumul_demand - cumul_power) * final_energy_value_per_kwh)
        return heating_power

