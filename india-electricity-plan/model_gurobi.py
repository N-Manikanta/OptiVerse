import gurobipy as gp
from gurobipy import GRB
import pandas as pd
import time
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def log(message):
    logging.info(message)

def format_inr(amount):
    """Format amount in lakh crores or crores based on the value."""
    if amount >= 1e5:
        return f"₹{amount/1e5:.2f} lakh crore"
    else:
        return f"₹{amount:.2f} crore"

log("Initiating India's Energy Transition Optimization Model")

log("Loading data from CSV files...")
emissions_data = pd.read_csv("india-electricity-plan/indian_electricity_sources_with_emissions.csv")
potential_plants_data = pd.read_csv("india-electricity-plan/potential_energy_plants_india.csv")
goals_data = pd.read_csv("india-electricity-plan/final_government_goals_2030_with_percent.csv")
log("Data loading complete. Proceeding to model initialization.")

log("Initializing Gurobi optimization model...")
model = gp.Model("India's Energy Transition Strategy")

log("Extracting and processing relevant data...")
sources = emissions_data["Source"].tolist()
years = list(range(2024, 2031))
log(f"Planning horizon: {years[0]} to {years[-1]}")

capital_cost = dict(zip(emissions_data["Source"], emissions_data["Capital Cost (₹/MW)"]))
current_capacity = dict(zip(emissions_data["Source"], emissions_data["Current Production (MW)"]))
emission_factors = dict(zip(emissions_data["Source"], emissions_data["Emission Factor (kg CO2/kWh)"]))
capacity_factors = {
    "Solar": 0.2, "Wind": 0.3, "Hydropower": 0.4, "Biomass": 0.7,
    "Nuclear": 0.9, "Coal": 0.8, "Natural Gas": 0.5,
}

log("Mapping energy goals to specific sources...")
goal_mappings = {
    "Coal Capacity (GW)": "Coal",
    "Natural Gas Capacity (GW)": "Natural Gas",
    "Nuclear Capacity (GW)": "Nuclear",
    "Solar Capacity (GW)": "Solar",
    "Wind Capacity (GW)": "Wind",
    "Hydropower Capacity (GW)": "Hydropower",
    "Biomass Capacity (GW)": "Biomass",
}

target_capacity = {
    goal_mappings[row["Goal"]]: row["Target Value"] * 1000
    for _, row in goals_data.iterrows()
    if row["Goal"] in goal_mappings
}

max_plants = dict(zip(potential_plants_data["Source"], potential_plants_data["Max Plants"]))
min_plants = dict(zip(potential_plants_data["Source"], potential_plants_data["Min Plants"]))

filtered_sources = [source for source in sources if source in target_capacity]
log(f"Energy sources considered in the model: {', '.join(filtered_sources)}")

log("Defining decision variables: Investment amounts for each source and year")
investment = model.addVars(filtered_sources, years, name="Investment", lb=0.0)

log("Introducing slack variables to handle potential constraint violations")
slack_target = model.addVars(filtered_sources, name="Slack_Target", lb=0.0)
slack_min_budget = model.addVars(years, name="Slack_Min_Budget", lb=0.0)
slack_max_budget = model.addVars(years, name="Slack_Max_Budget", lb=0.0)
slack_renewable_capacity = model.addVar(name="Slack_Renewable_Capacity", lb=0.0)
slack_renewable_production = model.addVar(name="Slack_Renewable_Production", lb=0.0)
slack_emissions = model.addVar(name="Slack_Emissions", lb=0.0)

log("Formulating the objective function: Minimize total investment and slack usage")
penalty_factor = 1.1
model.setObjective(
    gp.quicksum(investment[s, y] for s in filtered_sources for y in years) +
    penalty_factor * (
        gp.quicksum(slack_target[s] for s in filtered_sources) +
        gp.quicksum(slack_min_budget[y] + slack_max_budget[y] for y in years) +
        slack_renewable_capacity + slack_renewable_production + slack_emissions
    ),
    GRB.MINIMIZE
)

log("Adding constraints to the model...")
total_investment = {}
for source in filtered_sources:
    total_target_cost = (target_capacity[source] - current_capacity[source]) * capital_cost[source]

    model.addConstr(investment.sum(source, '*') + slack_target[source] >= total_target_cost, f"Target_{source}")

    total_investment[source] = total_target_cost
    log(f"Added investment constraints for {source}")

total_investment_budget = 44*10e11
average_annual_investment = total_investment_budget / len(years)
log(f"Total investment required: {format_inr(total_investment_budget / 1e7)}")
log(f"Average annual investment: {format_inr(average_annual_investment / 1e7)}")

min_annual_budget = 0.8 * average_annual_investment
max_annual_budget = 1.2 * average_annual_investment

for year in years:
    model.addConstr(investment.sum('*', year) + slack_min_budget[year] >= min_annual_budget, f"Min_Budget_{year}")
    model.addConstr(investment.sum('*', year) <= max_annual_budget + slack_max_budget[year], f"Max_Budget_{year}")
log("Added annual budget constraints")

log("Setting up renewable energy targets...")
renewable_sources = ["Solar", "Wind", "Hydropower", "Biomass"]
total_renewable_capacity_target = sum(target_capacity[source] for source in renewable_sources)
total_renewable_production_target = sum(
    (target_capacity[source] - current_capacity[source])
    * capacity_factors[source]
    * 8760
    / 1e6
    for source in renewable_sources
)

model.addConstr(
    gp.quicksum(investment[source, year] / capital_cost[source]
                for source in renewable_sources
                for year in years) + slack_renewable_capacity
    >= total_renewable_capacity_target,
    "Renewable_Capacity"
)

model.addConstr(
    gp.quicksum(investment[source, year] / capital_cost[source] * capacity_factors[source] * 8760
                for source in renewable_sources
                for year in years) + slack_renewable_production
    >= total_renewable_production_target * 1e6,
    "Renewable_Production"
)
log(f"Renewable capacity target: {total_renewable_capacity_target} MW")
log(f"Renewable production target: {total_renewable_production_target:.2f} billion units")

log("Setting up emission constraints...")
total_emission_target = 0.43 * total_renewable_production_target * 1e6 + sum(
    emission_factors[source]
    * (target_capacity[source] - current_capacity[source])
    * capacity_factors[source]
    * 8760
    / 1e3
    for source in filtered_sources
)

log(f"Emission factors: {emission_factors}")
log(f"Target capacities: {target_capacity}")
log(f"Current capacities: {current_capacity}")
log(f"Capacity factors: {capacity_factors}")

for source in filtered_sources:
    emission = (
        emission_factors[source]
        * (target_capacity[source] - current_capacity[source])
        * capacity_factors[source]
        * 8760
        / 1e3
    )
    log(f"Emission calculation for {source}: {emission:.2f} kg CO2")

log(f"Total emission target: {total_emission_target:.2f} kg CO2")

model.addConstr(
    gp.quicksum(investment[source, year] / capital_cost[source] * emission_factors[source]
                for source in filtered_sources
                for year in years)
    <= total_emission_target + slack_emissions,
    "Emissions"
)

log("Setting solver parameters for numerical stability")
model.setParam('FeasibilityTol', 1e-9)
model.setParam('NumericFocus', 3)

log("Optimizing the model...")
model.optimize()

if model.status == GRB.OPTIMAL:
    log("Optimal solution found! Analyzing results...")
    
    print("\n🌟 India's Optimal Energy Transition Strategy 🌟")
    print("================================================")
    
    total_investment_made = sum(investment[s, y].X for s in filtered_sources for y in years)
    print(f"\nTotal Investment: {format_inr(total_investment_made / 1e7)}")
    
    print("\nInvestment Breakdown by Source:")
    for source in filtered_sources:
        source_investment = sum(investment[source, y].X for y in years)
        if source_investment > 0:
            print(f"  {source}: {format_inr(source_investment / 1e7)}")
            for year in years:
                if investment[source, year].X > 0.5:
                    print(f"    {year}: {format_inr(investment[source, year].X / 1e7)}")
        
        if slack_target[source].X > 0.5:
            print(f"  ⚠️ {source} target shortfall: {format_inr(slack_target[source].X / 1e7)}")
    
    print("\nAnnual Budget Analysis:")
    for year in years:
        total_year_investment = sum(investment[s, year].X for s in filtered_sources)
        print(f"  {year}: {format_inr(total_year_investment / 1e7)}")
        if slack_min_budget[year].X > 0.5:
            print(f"    ⚠️ Min budget shortfall: {format_inr(slack_min_budget[year].X / 1e7)}")
        if slack_max_budget[year].X > 0.5:
            print(f"    ⚠️ Max budget exceeded by: {format_inr(slack_max_budget[year].X / 1e7)}")
    
    print("\nRenewable Energy Targets:")
    renewable_capacity_achieved = sum(investment[s, y].X / capital_cost[s] for s in renewable_sources for y in years)
    print(f"  Capacity: {renewable_capacity_achieved:.2f} MW / {total_renewable_capacity_target} MW")
    if slack_renewable_capacity.X > 0.5:
        print(f"    ⚠️ Shortfall: {slack_renewable_capacity.X:.2f} MW")
    
    renewable_production_achieved = sum(investment[s, y].X / capital_cost[s] * capacity_factors[s] * 8760 / 1e6 for s in renewable_sources for y in years)
    print(f"  Production: {renewable_production_achieved:.2f} BU / {total_renewable_production_target:.2f} BU")
    if slack_renewable_production.X > 0.5:
        print(f"    ⚠️ Shortfall: {slack_renewable_production.X / 1e6:.2f} BU")
    
    print("\nEmissions Analysis:")
    log("Calculating actual emissions based on the optimal solution...")
    emissions_produced = sum(
        (investment[source, year].X / capital_cost[source]) * emission_factors[source] * 8760
        for source in filtered_sources
        for year in years
    )
    log(f"Emissions calculation components:")
    for source in filtered_sources:
        for year in years:
            emission = investment[source, year].X / capital_cost[source] * emission_factors[source]
            log(f"  {source} in {year}: {emission:.2f} kg CO2")
    
    log(f"Total Emissions Produced: {emissions_produced:.2f} kg CO2")
    log(f"Emission Target: {total_emission_target:.2f} kg CO2")
    log(f"Slack Emissions: {slack_emissions.X:.2f} kg CO2")

    print(f"  Total Emissions: {emissions_produced:.2f} kg CO2 / {total_emission_target:.2f} kg CO2")
    if emissions_produced > total_emission_target:
        print(f"    ⚠️ Emissions exceed target by: {emissions_produced - total_emission_target:.2f} kg CO2")
    else:
        print(f"    Emissions are below target by: {total_emission_target - emissions_produced:.2f} kg CO2")
    
    print("\nKey Insights:")
    print("  1. The model suggests a significant investment in renewable energy sources.")
    print("  2. Some constraints needed to be relaxed to find a feasible solution.")
    print("  3. Careful consideration of the trade-offs between different energy sources is crucial.")
    print("  4. The emission target poses a significant challenge and may require additional measures.")
    
    log("Analysis complete. The model provides a roadmap for India's energy transition.")
else:
    log("Model could not be solved to optimality. Status: " + str(model.status))
    log("Please review the model constraints and data for potential issues.")