import pulp

def optimize_power_consumption(battery_capacity,
                               hourly_prices,
                               initial_soc,
                               final_energy_value_per_kwh,
                               hourly_consumption,
                               efficiency,
                               cost_of_cycle_kwh,
                               max_charge_power,
                               max_discharge_power):

    # Create a linear programming problem
    problem = pulp.LpProblem("Power_Optimization", pulp.LpMinimize)

    # Define decision variables
    n = len(hourly_prices)
    hours = range(n)

    charge_rate = pulp.LpVariable.dicts("ChargeRate", hours, lowBound = 0, upBound= max_charge_power)
    discharge_rate = pulp.LpVariable.dicts("DischargeRate", hours, lowBound = -max_discharge_power, upBound=0)
    
    soc = pulp.LpVariable.dicts("SOC", hours, lowBound=0, upBound=battery_capacity)

    # Define the objective function to minimize cost
    # TODO: debug hot to add costs of cycle to equation
    electricity_cost = pulp.lpSum(hourly_prices[hour] * (hourly_consumption[hour] + charge_rate[hour] + discharge_rate[hour] * efficiency) for hour in hours)
    amortization_cost = pulp.lpSum( (-discharge_rate[hour]) * cost_of_cycle_kwh for hour in hours)
    remaining_value = soc[n - 1] * efficiency * final_energy_value_per_kwh
    daily_cost = electricity_cost + amortization_cost - remaining_value

    problem += daily_cost

    # Define constraints
    for hour in hours:
      if hour == 0: init_soc = initial_soc
      else: init_soc = soc[hour -1]
      problem += soc[hour] == init_soc + charge_rate[hour] + discharge_rate[hour] # Conservation of charge 
      problem += soc[hour] <= battery_capacity # battery soc does not exceed capacity
      problem += soc[hour] >= 2 # battery soc should not go lower than that

    # Solve the linear programming problem
    status = problem.solve()



    # Extract the optimal solution
    optimization_result = {
        "Hour": [],
        "HourlyPrices": hourly_prices,
        "ChargeRate": [],
        "DischargeRate": [],
        "Projected_SOC": [],
    }

    optimization_res = []

    print(f"Optimization produced result: {pulp.LpStatus[status]}")

    for hour in hours:
        optimization_result["Hour"].append(hour)
        optimization_result["ChargeRate"].append(charge_rate[hour].varValue)
        optimization_result["DischargeRate"].append(discharge_rate[hour].varValue)
        optimization_result["Projected_SOC"].append(soc[hour].varValue)
   
    for key, value in optimization_result.items():
        print("  ", key, value)

    return optimization_result

# Example usage:
battery_capacity = 15  # kWh
#hourly_prices = p
hourly_prices = [15, 18, 19, 10, 10, 10, 10, 10, 10, 10, 10, 12, 14, 15, 15, 13, 14, 12, 15, 14, 13, 11, 10, 13]
initial_soc = 10  # kWh
final_energy_value_per_kwh = 12 #  Estimate value of energy the first hour next day that helps us to decide what stateof charge should be left.
hourly_consumption = [1.2] * len(hourly_prices)
cost_of_cycle_kwh = 1 # cents it costs per charge/discharge kwh (not cycle) of the battery (depreciacion costs)
max_charge_power = 5 # maximum power in KW that battery can be charged with
max_discharge_power = 5 # maximum power in KW that battery can be discharged at
efficiency = 0.95

result = optimize_power_consumption(battery_capacity, hourly_prices, initial_soc, final_energy_value_per_kwh, hourly_consumption, efficiency, cost_of_cycle_kwh, max_charge_power, max_discharge_power)
