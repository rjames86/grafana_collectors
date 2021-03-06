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
	"time"

	"github.com/avast/retry-go"
	influxdb2 "github.com/influxdata/influxdb-client-go/v2"
	"github.com/sethvargo/go-envconfig"

	"ecobee_influx_connector/ecobee" // taken from https://github.com/rspier/go-ecobee and lightly customized
)

type Config struct {
	APIKey             string   `env:"API_KEY,required"`
	WorkDir            string   `env:"WORK_DIR"`
	ThermostatID       []string `env:"THERMOSTAT_ID,required"`
	InfluxServer       string   `env:"INFLUX_SERVER,required"`
	InfluxOrg          string   `env:"INFLUX_ORG"`
	InfluxUser         string   `env:"INFLUX_USER"`
	InfluxPass         string   `env:"INFLUX_PASSWORD"`
	InfluxToken        string   `env:"INFLUX_TOKEN"`
	InfluxBucket       string   `env:"INFLUX_BUCKET,required"`
	WriteHeatPump1     bool     `env:"WRITE_HEAT_PUMP_1"`
	WriteHeatPump2     bool     `env:"WRITE_HEAT_PUMP_2"`
	WriteAuxHeat1      bool     `env:"WRITE_AUX_HEAT_1"`
	WriteAuxHeat2      bool     `env:"WRITE_AUX_HEAT_2"`
	WriteCool1         bool     `env:"WRITE_COOL_1"`
	WriteCool2         bool     `env:"WRITE_COOL_2"`
	WriteHumidifier    bool     `env:"WRITE_HUMIDIFIER"`
	AlwaysWriteWeather bool     `env:"ALWAYS_WRITE_WEATHER_AS_CURRENT"`
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

func main() {
	var listThermostats = flag.Bool("list-thermostats", false, "List available thermostats, then exit.")
	flag.Parse()

	var config Config
	l := envconfig.PrefixLookuper("ECOBEE_", envconfig.OsLookuper())
	if err := envconfig.ProcessWith(context.Background(), &config, l); err != nil {
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

	if len(config.ThermostatID) == 0 {
		log.Fatalf("thermostat_id must be set in the config file.")
	}
	if config.InfluxBucket == "" || config.InfluxServer == "" {
		log.Fatalf("influx_server and influx_bucket must be set in the config file.")
	}

	const influxTimeout = 3 * time.Second
	authString := ""
	if config.InfluxUser != "" || config.InfluxPass != "" {
		authString = fmt.Sprintf("%s:%s", config.InfluxUser, config.InfluxPass)
	} else if config.InfluxToken != "" {
		authString = fmt.Sprintf("%s", config.InfluxToken)
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
	_ = influxWriteApi

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

						fmt.Printf("Thermostat conditions at %s:\n", reportTime)
						fmt.Printf("\tcurrent climate setting: %s\n", currentComfortSetting)
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
									"comfort_setting":    currentComfortSetting,
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

	doUpdate()
	for {
		select {
		case <-time.Tick(5 * time.Minute):
			doUpdate()
		}
	}
}
