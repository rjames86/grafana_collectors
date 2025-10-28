package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"math"
	"os"
	"path"
	"strconv"
	"strings"
	"time"

	"github.com/avast/retry-go"
	influxdb2 "github.com/influxdata/influxdb-client-go/v2"
	"github.com/influxdata/influxdb-client-go/v2/api"
	"github.com/sethvargo/go-envconfig"

	"ecobee_influx_connector/ecobee" // taken from https://github.com/rspier/go-ecobee and lightly customized
)

type Config struct {
	// Ecobee-specific settings (use ECOBEE_ prefix)
	APIKey             string   `env:"ECOBEE_API_KEY,required"`
	WorkDir            string   `env:"ECOBEE_WORK_DIR"`
	ThermostatID       []string `env:"ECOBEE_THERMOSTAT_ID"` // No longer required - will auto-discover
	WriteHeatPump1     bool     `env:"ECOBEE_WRITE_HEAT_PUMP_1"`
	WriteHeatPump2     bool     `env:"ECOBEE_WRITE_HEAT_PUMP_2"`
	WriteAuxHeat1      bool     `env:"ECOBEE_WRITE_AUX_HEAT_1"`
	WriteAuxHeat2      bool     `env:"ECOBEE_WRITE_AUX_HEAT_2"`
	WriteCool1         bool     `env:"ECOBEE_WRITE_COOL_1"`
	WriteCool2         bool     `env:"ECOBEE_WRITE_COOL_2"`
	WriteHumidifier    bool     `env:"ECOBEE_WRITE_HUMIDIFIER"`
	AlwaysWriteWeather bool     `env:"ECOBEE_ALWAYS_WRITE_WEATHER_AS_CURRENT"`

	// InfluxDB settings (use standard WEATHERFLOW_COLLECTOR_ prefix)
	InfluxServer       string   `env:"WEATHERFLOW_COLLECTOR_INFLUXDB_URL,required"`
	InfluxOrg          string   `env:"WEATHERFLOW_COLLECTOR_INFLUXDB_ORG"`
	InfluxToken        string   `env:"WEATHERFLOW_COLLECTOR_INFLUXDB_TOKEN"`
	InfluxBucket       string   `env:"ECOBEE_INFLUX_BUCKET,required"`
}

const (
	thermostatNameTag = "thermostat_name"
)

// WindChill calculates the wind chill for the given temperature (in Fahrenheit)
// and wind speed (in miles/hour). If wind speed is less than 3 mph, or temperature
// if over 50 degrees, the given temperature is returned - the forumla works
// below 50 degrees and above 3 mph.
func WindChill(tempF, windSpeedMph float64) float64 {
	if tempF > 50.0 || windSpeedMph < 3.0 {
		return tempF
	}
	return 35.74 + (0.6215 * tempF) - (35.75 * math.Pow(windSpeedMph, 0.16)) + (0.4275 * tempF * math.Pow(windSpeedMph, 0.16))
}

// IndoorHumidityRecommendation returns the maximum recommended indoor relative
// humidity percentage for the given outdoor temperature (in degrees F).
func IndoorHumidityRecommendation(outdoorTempF float64) int {
	if outdoorTempF >= 50 {
		return 50
	}
	if outdoorTempF >= 40 {
		return 45
	}
	if outdoorTempF >= 30 {
		return 40
	}
	if outdoorTempF >= 20 {
		return 35
	}
	if outdoorTempF >= 10 {
		return 30
	}
	if outdoorTempF >= 0 {
		return 25
	}
	if outdoorTempF >= -10 {
		return 20
	}
	return 15
}

// generateDateChunks splits a date range into 31-day chunks for the Runtime Report API
func generateDateChunks(startDate, endDate string) ([][]string, error) {
	start, err := time.Parse("2006-01-02", startDate)
	if err != nil {
		return nil, err
	}
	end, err := time.Parse("2006-01-02", endDate)
	if err != nil {
		return nil, err
	}

	var chunks [][]string
	current := start

	for current.Before(end) || current.Equal(end) {
		chunkEnd := current.AddDate(0, 0, 30) // 31 days (0-30)
		if chunkEnd.After(end) {
			chunkEnd = end
		}

		chunks = append(chunks, []string{
			current.Format("2006-01-02"),
			chunkEnd.Format("2006-01-02"),
		})

		current = chunkEnd.AddDate(0, 0, 1) // Move to next day after chunk end
	}

	return chunks, nil
}

// discoverThermostats finds all thermostats registered to the account
func discoverThermostats(client *ecobee.Client) ([]string, error) {
	s := ecobee.Selection{
		SelectionType: "registered",
	}
	ts, err := client.GetThermostats(s)
	if err != nil {
		return nil, err
	}

	var thermostatIDs []string
	for _, t := range ts {
		thermostatIDs = append(thermostatIDs, t.Identifier)
		log.Printf("Discovered thermostat: '%s' (ID: %s)", t.Name, t.Identifier)
	}

	return thermostatIDs, nil
}

// findEarliestAvailableDate attempts to find the earliest date with available data
func findEarliestAvailableDate(client *ecobee.Client, thermostatId string) (string, error) {
	columns := "zoneAveTemp,zoneCoolTemp,zoneHeatTemp"

	// Test candidate dates from most recent to older
	candidateDates := []string{
		"2024-01-01",
		"2023-01-01",
		"2022-01-01",
		"2021-10-01",
		"2021-01-01",
	}

	for _, testDate := range candidateDates {
		endTime, err := time.Parse("2006-01-02", testDate)
		if err != nil {
			continue
		}
		testEnd := endTime.AddDate(0, 0, 7).Format("2006-01-02")

		_, err = client.GetRuntimeReport(thermostatId, testDate, testEnd, columns, false)
		if err == nil {
			return testDate, nil
		}
		time.Sleep(200 * time.Millisecond)
	}

	return "", fmt.Errorf("no historical data found")
}

// runBackfill processes historical data using the Runtime Report API
func runBackfill(client *ecobee.Client, influxWriteApi api.WriteAPIBlocking, config Config, startDate, endDate string) error {
	chunks, err := generateDateChunks(startDate, endDate)
	if err != nil {
		return fmt.Errorf("failed to generate date chunks: %v", err)
	}

	log.Printf("Processing %d date chunks from %s to %s", len(chunks), startDate, endDate)

	// Pre-fetch thermostat details to get comfort setting names (avoids repeated auth)
	log.Println("Fetching thermostat details...")
	thermostatComfortSettings := make(map[string]map[string]string)
	for _, thermostatId := range config.ThermostatID {
		t, err := client.GetThermostat(thermostatId)
		if err != nil {
			log.Printf("Warning: Failed to get details for thermostat %s: %v", thermostatId, err)
			thermostatComfortSettings[thermostatId] = make(map[string]string)
			continue
		}

		comfortSettings := make(map[string]string)
		for _, climate := range t.Program.Climates {
			comfortSettings[climate.ClimateRef] = climate.Name
		}
		thermostatComfortSettings[thermostatId] = comfortSettings
	}

	// Request full equipment runtime data for backfill
	columns := "zoneAveTemp,zoneCoolTemp,zoneHeatTemp,fan,hvacMode,zoneCalendarEvent,auxHeat1,auxHeat2,compHeat1,compHeat2,compCool1,compCool2,humidifier"

	successfulChunks := 0
	consecutiveFailures := 0

	for i, chunk := range chunks {
		if i%10 == 0 || i == len(chunks)-1 {
			log.Printf("Processing chunk %d/%d: %s to %s", i+1, len(chunks), chunk[0], chunk[1])
		}

		chunkSuccess := false
		for _, thermostatId := range config.ThermostatID {
			report, err := client.GetRuntimeReport(thermostatId, chunk[0], chunk[1], columns, false)
			if err != nil {
				continue
			}

			comfortSettings := thermostatComfortSettings[thermostatId]
			if err := processRuntimeReport(report, influxWriteApi, config, thermostatId, comfortSettings); err != nil {
				continue
			}

			chunkSuccess = true
		}

		if chunkSuccess {
			successfulChunks++
			consecutiveFailures = 0
		} else {
			consecutiveFailures++
			if consecutiveFailures >= 10 {
				log.Printf("Warning: %d consecutive chunks failed starting at %s", consecutiveFailures, chunk[0])
				break
			}
		}

		time.Sleep(500 * time.Millisecond)
	}

	log.Printf("Backfill completed: %d/%d chunks processed successfully", successfulChunks, len(chunks))

	return nil
}

// processRuntimeReport processes a runtime report and writes data to InfluxDB
func processRuntimeReport(report *ecobee.RuntimeReport, influxWriteApi api.WriteAPIBlocking, config Config, thermostatId string, comfortSettings map[string]string) error {
	const influxTimeout = 3 * time.Second

	// Runtime reports have fixed format: date, time, then requested columns in order
	// We requested: "zoneAveTemp,zoneCoolTemp,zoneHeatTemp,fan,hvacMode,zoneCalendarEvent,auxHeat1,auxHeat2,compHeat1,compHeat2,compCool1,compCool2,humidifier"
	columnMap := map[string]int{
		"date":               0, // Always first
		"time":               1, // Always second
		"zoneAveTemp":        2,
		"zoneCoolTemp":       3,
		"zoneHeatTemp":       4,
		"fan":                5,
		"hvacMode":           6,
		"zoneCalendarEvent":  7,
		"auxHeat1":           8,
		"auxHeat2":           9,
		"compHeat1":          10, // Heat pump stage 1
		"compHeat2":          11, // Heat pump stage 2
		"compCool1":          12, // Cooling stage 1
		"compCool2":          13, // Cooling stage 2
		"humidifier":         14,
	}

	// Process each row (5-minute interval)
	for _, row := range report.RowList {
		fields := strings.Split(row, ",")
		if len(fields) < 15 {
			continue // Skip malformed rows (need at least 15 fields: date,time + 13 data columns)
		}

		// Parse timestamp from date and time fields
		dateTimeStr := fields[0] + " " + fields[1]
		reportTime, err := time.Parse("2006-01-02 15:04:05", dateTimeStr)
		if err != nil {
			continue // Skip rows with invalid timestamps
		}

		// Helper function to get float value from column
		getFloat := func(colName string) (float64, error) {
			if idx, ok := columnMap[colName]; ok && idx < len(fields) {
				if fields[idx] == "" {
					return 0, nil
				}
				val, err := strconv.ParseFloat(fields[idx], 64)
				if err != nil {
					return 0, err
				}
				return val / 10.0, nil // Ecobee stores temps in tenths
			}
			return 0, fmt.Errorf("column %s not found", colName)
		}

		// Helper function to get integer value from column
		getInt := func(colName string) (int, error) {
			if idx, ok := columnMap[colName]; ok && idx < len(fields) {
				if fields[idx] == "" {
					return 0, nil
				}
				return strconv.Atoi(fields[idx])
			}
			return 0, fmt.Errorf("column %s not found", colName)
		}

		// Helper function to get string value from column
		getString := func(colName string) string {
			if idx, ok := columnMap[colName]; ok && idx < len(fields) {
				return fields[idx]
			}
			return ""
		}

		// Parse values from the runtime report (only use columns we're requesting)
		currentTemp, _ := getFloat("zoneAveTemp")
		heatSetPoint, _ := getFloat("zoneHeatTemp")
		coolSetPoint, _ := getFloat("zoneCoolTemp")

		// These columns may not be available in simplified mode
		demandMgmtOffset := 0.0 // dmOffset not available in simplified mode
		hvacMode := getString("hvacMode")

		fanRunSec, _ := getInt("fan")
		// Equipment runtime data from Runtime Report API
		auxHeat1RunSec, _ := getInt("auxHeat1")
		auxHeat2RunSec, _ := getInt("auxHeat2")
		heatPump1RunSec, _ := getInt("compHeat1") // Heat pump stage 1 = compressor heating stage 1
		heatPump2RunSec, _ := getInt("compHeat2") // Heat pump stage 2 = compressor heating stage 2
		cool1RunSec, _ := getInt("compCool1")
		cool2RunSec, _ := getInt("compCool2")
		humidifierRunSec, _ := getInt("humidifier")

		// Get comfort setting name
		comfortSettingRef := getString("zoneCalendarEvent")
		comfortSettingName := comfortSettings[comfortSettingRef]
		if comfortSettingName == "" {
			comfortSettingName = comfortSettingRef // fallback to ref
		}

		// Write to InfluxDB using the same structure as the normal collector
		if err := retry.Do(func() error {
			ctx, cancel := context.WithTimeout(context.Background(), influxTimeout)
			defer cancel()

			influxFields := map[string]interface{}{
				"temperature":        currentTemp,
				"heat_set_point":     heatSetPoint,
				"cool_set_point":     coolSetPoint,
				"demand_mgmt_offset": demandMgmtOffset,
				"fan_run_time":       fanRunSec,
				"comfort_setting":    comfortSettingName,
				"hvac_mode":         hvacMode, // Add HVAC mode since we have it
			}

			if config.WriteAuxHeat1 {
				influxFields["aux_heat_1_run_time"] = auxHeat1RunSec
			}
			if config.WriteAuxHeat2 {
				influxFields["aux_heat_2_run_time"] = auxHeat2RunSec
			}
			if config.WriteHeatPump1 {
				influxFields["heat_pump_1_run_time"] = heatPump1RunSec
			}
			if config.WriteHeatPump2 {
				influxFields["heat_pump_2_run_time"] = heatPump2RunSec
			}
			if config.WriteCool1 {
				influxFields["cool_1_run_time"] = cool1RunSec
			}
			if config.WriteCool2 {
				influxFields["cool_2_run_time"] = cool2RunSec
			}
			if config.WriteHumidifier {
				influxFields["humidifier_run_time"] = humidifierRunSec
			}

			point := influxdb2.NewPoint(
				"ecobee_runtime",
				map[string]string{thermostatNameTag: thermostatId},
				influxFields,
				reportTime,
			)

			return influxWriteApi.WritePoint(ctx, point)
		}); err != nil {
			log.Printf("Warning: Failed to write data point for %s: %v", reportTime, err)
		}
	}

	return nil
}

func main() {
	var listThermostats = flag.Bool("list-thermostats", false, "List available thermostats, then exit.")
	var backfill = flag.Bool("backfill", false, "Run in backfill mode to import historical data.")
	var startDate = flag.String("start-date", "", "Start date for backfill (YYYY-MM-DD format).")
	var endDate = flag.String("end-date", "", "End date for backfill (YYYY-MM-DD format).")
	var autoDetectStart = flag.Bool("auto-detect-start", false, "Auto-detect the earliest available date for backfill.")
	flag.Parse()

	var config Config
	// Process config without prefix to get both ECOBEE_ and WEATHERFLOW_COLLECTOR_ variables
	if err := envconfig.Process(context.Background(), &config); err != nil {
		panic(err)
	}

	if config.APIKey == "" {
		log.Fatal("api_key must be set in the config file.")
	}
	if config.WorkDir == "" {
		wd, err := os.Getwd()
		if err != nil {
			log.Fatalf("Unable to get current working directory: %s", err)
		}
		config.WorkDir = wd
	}

	client := ecobee.NewClient(config.APIKey, path.Join(config.WorkDir, "ecobee-cred-cache"))

	if *listThermostats {
		s := ecobee.Selection{
			SelectionType: "registered",
		}
		ts, err := client.GetThermostats(s)
		if err != nil {
			log.Fatal(err)
		}
		for _, t := range ts {
			fmt.Printf("'%s': ID %s\n", t.Name, t.Identifier)
		}
		os.Exit(0)
	}

	// Auto-discover thermostats if none specified
	if len(config.ThermostatID) == 0 {
		log.Println("No thermostat IDs specified, discovering all thermostats...")
		thermostatIDs, err := discoverThermostats(client)
		if err != nil {
			log.Fatalf("Failed to discover thermostats: %v", err)
		}
		if len(thermostatIDs) == 0 {
			log.Fatal("No thermostats found in account")
		}
		config.ThermostatID = thermostatIDs
		log.Printf("Found %d thermostat(s)", len(thermostatIDs))
	}
	if config.InfluxBucket == "" || config.InfluxServer == "" {
		log.Fatalf("WEATHERFLOW_COLLECTOR_INFLUXDB_URL and ECOBEE_INFLUX_BUCKET must be set.")
	}

	const influxTimeout = 3 * time.Second
	authString := config.InfluxToken
	if authString == "" {
		log.Fatalf("WEATHERFLOW_COLLECTOR_INFLUXDB_TOKEN must be set.")
	}
	influxClient := influxdb2.NewClient(config.InfluxServer, authString)
	ctx, cancel := context.WithTimeout(context.Background(), influxTimeout)
	defer cancel()
	health, err := influxClient.Health(ctx)
	if err != nil {
		log.Fatalf("failed to check InfluxDB health: %v", err)
	}
	if health.Status != "pass" {
		log.Fatalf("InfluxDB did not pass health check: status %s; message '%s'", health.Status, *health.Message)
	}
	influxWriteApi := influxClient.WriteAPIBlocking(config.InfluxOrg, config.InfluxBucket)

	// Validate backfill parameters
	if *backfill {
		if *autoDetectStart {
			if *endDate == "" {
				log.Fatal("-end-date must be specified when using -auto-detect-start")
			}
			if _, err := time.Parse("2006-01-02", *endDate); err != nil {
				log.Fatalf("Invalid end-date format: %v. Use YYYY-MM-DD", err)
			}
		} else {
			if *startDate == "" || *endDate == "" {
				log.Fatal("Both -start-date and -end-date must be specified for backfill mode (or use -auto-detect-start)")
			}
			if _, err := time.Parse("2006-01-02", *startDate); err != nil {
				log.Fatalf("Invalid start-date format: %v. Use YYYY-MM-DD", err)
			}
			if _, err := time.Parse("2006-01-02", *endDate); err != nil {
				log.Fatalf("Invalid end-date format: %v. Use YYYY-MM-DD", err)
			}
		}
	}

	type UpdateInterval struct {
		LastWrittenRuntimeInterval int
		LastWrittenWeather         time.Time
		LastWrittenSensors         time.Time
	}
	lastUpdates := map[string]*UpdateInterval{}

	for _, thermostatId := range config.ThermostatID {
		lastUpdates[thermostatId] = &UpdateInterval{
			LastWrittenRuntimeInterval: 0,
			LastWrittenWeather:         time.Time{},
			LastWrittenSensors:         time.Time{},
		}
	}

	doUpdate := func() {
		if err := retry.Do(
			func() error {
				for _, thermostatId := range config.ThermostatID {
					fmt.Println("Grabbing thermostat ", thermostatId)
					t, err := client.GetThermostat(thermostatId)
					if err != nil {
						return err
					}

					latestRuntimeInterval := t.ExtendedRuntime.RuntimeInterval
					log.Printf("latest runtime interval available is %d\n", latestRuntimeInterval)

					// In the absence of a time zone indicator, Parse returns a time in UTC.
					baseReportTime, err := time.Parse("2006-01-02 15:04:05", t.ExtendedRuntime.LastReadingTimestamp)
					if err != nil {
						return err
					}

					for i := 0; i < 3; i++ {
						reportTime := baseReportTime
						if i == 0 {
							reportTime = reportTime.Add(-5 * time.Minute)
						}
						if i == 2 {
							reportTime = reportTime.Add(5 * time.Minute)
						}

						currentTemp := float64(t.ExtendedRuntime.ActualTemperature[i]) / 10.0
						currentHumidity := t.ExtendedRuntime.ActualHumidity[i]
						heatSetPoint := float64(t.ExtendedRuntime.DesiredHeat[i]) / 10.0
						coolSetPoint := float64(t.ExtendedRuntime.DesiredCool[i]) / 10.0
						humiditySetPoint := t.ExtendedRuntime.DesiredHumidity[i]
						demandMgmtOffset := float64(t.ExtendedRuntime.DmOffset[i]) / 10.0
						hvacMode := t.ExtendedRuntime.HvacMode[i] // string :(
						heatPump1RunSec := t.ExtendedRuntime.HeatPump1[i]
						heatPump2RunSec := t.ExtendedRuntime.HeatPump1[i]
						auxHeat1RunSec := t.ExtendedRuntime.AuxHeat1[i]
						auxHeat2RunSec := t.ExtendedRuntime.AuxHeat2[i]
						cool1RunSec := t.ExtendedRuntime.Cool1[i]
						cool2RunSec := t.ExtendedRuntime.Cool2[i]
						fanRunSec := t.ExtendedRuntime.Fan[i]
						humidifierRunSec := t.ExtendedRuntime.Humidifier[i]

						currentComfortSetting := t.Program.CurrentClimateRef
						currentComfortSettingName := ""
						for _, climate := range t.Program.Climates {
							if climate.ClimateRef == currentComfortSetting {
								currentComfortSettingName = climate.Name
								break
							}
						}
						if currentComfortSettingName == "" {
							currentComfortSettingName = currentComfortSetting // fallback to ref if name not found
						}

						fmt.Printf("Thermostat conditions at %s:\n", reportTime)
						fmt.Printf("\tcurrent climate setting: %s\n", currentComfortSettingName)
						fmt.Printf("\tcurrent temperature: %.1f degF\n\theat set point: %.1f degF\n\tcool set point: %.1f degF\n\tdemand management offset: %.1f\n",
							currentTemp, heatSetPoint, coolSetPoint, demandMgmtOffset)
						fmt.Printf("\tcurrent humidity: %d%%\n\thumidity set point: %d\n\tHVAC mode: %s\n",
							currentHumidity, humiditySetPoint, hvacMode)
						fmt.Printf("\tfan runtime: %d seconds\n\thumidifier runtime: %d seconds\n",
							fanRunSec, humidifierRunSec)
						fmt.Printf("\theat pump 1 runtime: %d seconds\n\theat pump 2 runtime: %d seconds\n",
							heatPump1RunSec, heatPump2RunSec)
						fmt.Printf("\theat 1 runtime: %d seconds\n\theat 2 runtime: %d seconds\n",
							auxHeat1RunSec, auxHeat2RunSec)
						fmt.Printf("\tcool 1 runtime: %d seconds\n\tcool 2 runtime: %d seconds\n",
							cool1RunSec, cool2RunSec)

						if latestRuntimeInterval != lastUpdates[thermostatId].LastWrittenRuntimeInterval {
							fmt.Printf("Updating ecobee_runtime")
							if err := retry.Do(func() error {
								ctx, cancel := context.WithTimeout(context.Background(), influxTimeout)
								defer cancel()
								fields := map[string]interface{}{
									"temperature":        currentTemp,
									"humidity":           currentHumidity,
									"heat_set_point":     heatSetPoint,
									"cool_set_point":     coolSetPoint,
									"demand_mgmt_offset": demandMgmtOffset,
									"fan_run_time":       fanRunSec,
									"comfort_setting":    currentComfortSettingName,
								}
								if config.WriteHumidifier {
									fields["humidity_set_point"] = humiditySetPoint
									fields["humidifier_run_time"] = humidifierRunSec
								}
								if config.WriteAuxHeat1 {
									fields["aux_heat_1_run_time"] = auxHeat1RunSec
								}
								if config.WriteAuxHeat2 {
									fields["aux_heat_2_run_time"] = auxHeat2RunSec
								}
								if config.WriteHeatPump1 {
									fields["heat_pump_1_run_time"] = heatPump1RunSec
								}
								if config.WriteHeatPump2 {
									fields["heat_pump_2_run_time"] = heatPump2RunSec
								}
								if config.WriteCool1 {
									fields["cool_1_run_time"] = cool1RunSec
								}
								if config.WriteCool2 {
									fields["cool_2_run_time"] = cool2RunSec
								}
								err := influxWriteApi.WritePoint(ctx,
									influxdb2.NewPoint(
										"ecobee_runtime",
										map[string]string{thermostatNameTag: t.Name}, // tags
										fields,
										reportTime,
									))
								if err != nil {
									return err
								}
								return nil
							}, retry.Attempts(2)); err != nil {
								return err
							}
						}
					}
					lastUpdates[thermostatId].LastWrittenRuntimeInterval = latestRuntimeInterval

					// assume t.LastModified for these:
					sensorTime, err := time.Parse("2006-01-02 15:04:05", t.UtcTime)
					if err != nil {
						return err
					}
					for _, sensor := range t.RemoteSensors {
						name := sensor.Name
						var temp float64
						var presence, presenceSupported bool
						for _, c := range sensor.Capability {
							if c.Type == "temperature" {
								tempInt, err := strconv.Atoi(c.Value)
								if err != nil {
									log.Printf("error reading temp '%s' for sensor %s: %s", c.Value, sensor.Name, err)
								} else {
									temp = float64(tempInt) / 10.0
								}
							} else if c.Type == "occupancy" {
								presenceSupported = true
								presence = c.Value == "true"
							}
						}
						fmt.Printf("Sensor '%s' at %s:\n", name, sensorTime)
						fmt.Printf("\ttemperature: %.1f degF\n", temp)
						if presenceSupported {
							fmt.Printf("\toccupied: %t\n", presence)
						}

						if temp == 0.0 {
							continue
						}

						if sensorTime != lastUpdates[thermostatId].LastWrittenSensors {
							fmt.Printf("Updating ecobee_sensor")
							if err := retry.Do(func() error {
								ctx, cancel := context.WithTimeout(context.Background(), influxTimeout)
								defer cancel()
								fields := map[string]interface{}{
									"temperature": temp,
								}
								if presenceSupported {
									fields["occupied"] = presence
								}
								err := influxWriteApi.WritePoint(ctx,
									influxdb2.NewPoint(
										"ecobee_sensor",
										map[string]string{
											thermostatNameTag: t.Name,
											"sensor_name":     sensor.Name,
											"sensor_id":       sensor.ID,
										}, // tags
										fields,
										sensorTime,
									))
								if err != nil {
									return err
								}
								return nil
							}, retry.Attempts(2)); err != nil {
								return err
							}
						}
					}
					lastUpdates[thermostatId].LastWrittenSensors = sensorTime

					weatherTime, err := time.Parse("2006-01-02 15:04:05", t.Weather.Timestamp)
					if err != nil {
						return err
					}
					outdoorTemp := float64(t.Weather.Forecasts[0].Temperature) / 10.0
					pressureMillibar := t.Weather.Forecasts[0].Pressure
					outdoorHumidity := t.Weather.Forecasts[0].RelativeHumidity
					dewpoint := float64(t.Weather.Forecasts[0].Dewpoint) / 10.0
					windspeedMph := t.Weather.Forecasts[0].WindSpeed
					windBearing := t.Weather.Forecasts[0].WindBearing
					visibilityMeters := t.Weather.Forecasts[0].Visibility
					visibilityMiles := float64(visibilityMeters) / 1609.34
					windChill := WindChill(outdoorTemp, float64(windspeedMph))

					fmt.Printf("Weather at %s:\n", weatherTime)
					fmt.Printf("\ttemperature: %.1f degF\n\tpressure: %d mb\n\thumidity: %d%%\n\tdew point: %.1f degF\n\twind: %d at %d mph\n\twind chill: %.1f degF\n\tvisibility: %.1f miles\n",
						outdoorTemp, pressureMillibar, outdoorHumidity, dewpoint, windBearing, windspeedMph, windChill, visibilityMiles)

					if weatherTime != lastUpdates[thermostatId].LastWrittenWeather || config.AlwaysWriteWeather {
						fmt.Printf("Updating ecobee_weather")
						if err := retry.Do(func() error {
							ctx, cancel := context.WithTimeout(context.Background(), influxTimeout)
							defer cancel()
							pointTime := weatherTime
							if config.AlwaysWriteWeather {
								pointTime = time.Now()
							}
							err := influxWriteApi.WritePoint(ctx,
								influxdb2.NewPoint(
									"ecobee_weather",
									map[string]string{thermostatNameTag: t.Name}, // tags
									map[string]interface{}{ // fields
										"outdoor_temp":                    outdoorTemp,
										"outdoor_humidity":                outdoorHumidity,
										"barometric_pressure_mb":          pressureMillibar,
										"barometric_pressure_inHg":        float64(pressureMillibar) / 33.864,
										"dew_point":                       dewpoint,
										"wind_speed":                      windspeedMph,
										"wind_bearing":                    windBearing,
										"visibility_mi":                   visibilityMiles,
										"recommended_max_indoor_humidity": IndoorHumidityRecommendation(outdoorTemp),
										"wind_chill_f":                    windChill,
									},
									pointTime,
								))
							if err != nil {
								return err
							}
							lastUpdates[thermostatId].LastWrittenWeather = weatherTime
							return nil
						}, retry.Attempts(2)); err != nil {
							return err
						}
					}
					fmt.Println("done getting for ", thermostatId)
				}
				return nil
			},
		); err != nil {
			log.Fatal(err)
		}
	}

	// Run backfill if requested
	if *backfill {
		actualStartDate := *startDate

		// Auto-detect earliest available date if requested
		if *autoDetectStart {
			log.Println("Auto-detecting earliest available date...")

			overallEarliest := ""
			for _, thermostatId := range config.ThermostatID {
				earliest, err := findEarliestAvailableDate(client, thermostatId)
				if err != nil {
					log.Printf("Warning: No historical data found for thermostat %s", thermostatId)
					continue
				}

				log.Printf("Thermostat %s: data available from %s", thermostatId, earliest)

				if overallEarliest == "" {
					overallEarliest = earliest
				} else {
					earliestTime, _ := time.Parse("2006-01-02", earliest)
					overallTime, _ := time.Parse("2006-01-02", overallEarliest)
					if earliestTime.Before(overallTime) {
						overallEarliest = earliest
					}
				}
			}

			if overallEarliest == "" {
				log.Fatal("Could not determine earliest available date for any thermostat")
			}

			actualStartDate = overallEarliest
			log.Printf("Using start date: %s", actualStartDate)
		}

		if err := runBackfill(client, influxWriteApi, config, actualStartDate, *endDate); err != nil {
			log.Fatalf("Backfill failed: %v", err)
		}
		log.Println("Backfill completed successfully")
		return
	}

	// Normal operation
	doUpdate()
	for {
		select {
		case <-time.Tick(5 * time.Minute):
			doUpdate()
		}
	}
}
