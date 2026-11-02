// ============================================================
// AutoGreen - Código Final
// Universidad Mariano Gálvez de Guatemala
// Arquitectura de Computadoras II
// Grupo 3
// ============================================================

#include <DHT.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// --- Pines sensores ---
#define PIN_DHT    2
#define PIN_DHT_VCC 7      // El DHT22 se alimenta desde este pin para poder reiniciarlo
#define PIN_SUELO  A0
#define PIN_LDR    A3
#define TIPO_DHT   DHT22

// --- Pines actuadores ---
#define PIN_VENTILADOR  8
#define PIN_BOMBA       9
#define PIN_CALEFACTOR  10
#define PIN_LUZ         11

// --- Umbrales de control (segun requerimientos del proyecto) ---
#define TEMP_MAX       35.0
#define TEMP_MIN       30.0
#define HUMEDAD_MIN    40
#define HUMEDAD_MAX    50
#define LUZ_ENCENDER   4000
#define LUZ_APAGAR     6000

// --- Reinicio automatico del DHT22 ---
#define FALLAS_PARA_REINICIO 3

// --- Sensor DHT22 ---
DHT dht(PIN_DHT, TIPO_DHT);

// --- LCD I2C direccion 0x27 ---
LiquidCrystal_I2C lcd(0x27, 16, 2);

// --- Variables globales ---
float temperatura;
float humedad_ambiente;
int   humedad_suelo;
float luz_lux;
int   fallasDHT = 0;
bool  dhtListo  = false;   // true despues de la primera lectura valida

// ============================================================
void setup() {
  Serial.begin(9600);

  // Encender el DHT22 desde su pin de alimentacion
  pinMode(PIN_DHT_VCC, OUTPUT);
  digitalWrite(PIN_DHT_VCC, HIGH);
  dht.begin();

  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("AutoGreen v1.0");
  lcd.setCursor(0, 1);
  lcd.print("Iniciando...");
  delay(2000);
  lcd.clear();

  pinMode(PIN_VENTILADOR, OUTPUT);
  pinMode(PIN_BOMBA,      OUTPUT);
  pinMode(PIN_CALEFACTOR, OUTPUT);
  pinMode(PIN_LUZ,        OUTPUT);

  digitalWrite(PIN_VENTILADOR, HIGH);
  digitalWrite(PIN_BOMBA,      HIGH);
  digitalWrite(PIN_LUZ,        HIGH);
  digitalWrite(PIN_CALEFACTOR, LOW);

  Serial.println("=== AutoGreen iniciando... ===");
}

// ============================================================
void loop() {
  delay(200);
  leerSensores();
  controlAutomatico();
  mostrarLCD();
  mostrarSerial();
  delay(500);
}

// ============================================================
void leerSensores() {
  // DHT22: temperatura y humedad ambiente
  // Si la lectura falla se conserva el ultimo valor valido.
  // Tras varias fallas seguidas se reinicia el sensor.
  float t = dht.readTemperature();
  float h = dht.readHumidity();

  if (isnan(t) || isnan(h)) {
    fallasDHT++;
    Serial.print("[ERROR] Fallo en lectura DHT22 (");
    Serial.print(fallasDHT);
    Serial.println(")");
    if (fallasDHT >= FALLAS_PARA_REINICIO) {
      reiniciarDHT();
    }
  } else {
    temperatura      = t;
    humedad_ambiente = h;
    fallasDHT        = 0;
    dhtListo         = true;
  }

  // Sensor capacitivo suelo: promedio 20 lecturas
  // Tierra seca = RAW ~1014 = 0%, Agua = RAW ~242 = 100%
  long suma = 0;
  for (int i = 0; i < 20; i++) {
    suma += analogRead(PIN_SUELO);
    delay(2);
  }
  int raw_suelo = suma / 20;
  humedad_suelo = map(raw_suelo, 1014, 242, 0, 100);
  humedad_suelo = constrain(humedad_suelo, 0, 100);

  // LDR: promedio 5 lecturas
  int sumaLDR = 0;
  for (int i = 0; i < 5; i++) {
    sumaLDR += analogRead(PIN_LDR);
    delay(2);
  }
  int raw_ldr = sumaLDR / 5;
  luz_lux = map(raw_ldr, 1014, 610, 0, 10000);
  luz_lux = constrain(luz_lux, 0, 10000);
}

// ============================================================
// Apaga el DHT22, espera y lo vuelve a encender
void reiniciarDHT() {
  Serial.println("[INFO] Reiniciando DHT22...");
  digitalWrite(PIN_DHT_VCC, LOW);
  pinMode(PIN_DHT, OUTPUT);       // evita que el sensor se alimente por la linea de datos
  digitalWrite(PIN_DHT, LOW);
  delay(1000);
  digitalWrite(PIN_DHT_VCC, HIGH);
  delay(2000);                    // el DHT22 necesita ~2 s para estabilizarse
  dht.begin();
  fallasDHT = 0;
}

// ============================================================
void controlAutomatico() {
  // Sin una lectura valida aun, ventilador y calefactor quedan apagados
  if (!dhtListo) {
    digitalWrite(PIN_VENTILADOR, HIGH);
    digitalWrite(PIN_CALEFACTOR, LOW);
  } else {
    // Ventilador: ON si temperatura > 30C
    digitalWrite(PIN_VENTILADOR, temperatura > TEMP_MAX ? LOW : HIGH);
    // Calefactor: ON si temperatura < 15C
    digitalWrite(PIN_CALEFACTOR, temperatura < TEMP_MIN ? HIGH : LOW);
  }

  // Bomba: histeresis ON < 40%, OFF > 60%
  if (humedad_suelo < HUMEDAD_MIN) {
    digitalWrite(PIN_BOMBA, LOW);
  } else if (humedad_suelo > HUMEDAD_MAX) {
    digitalWrite(PIN_BOMBA, HIGH);
  }

  // Luz auxiliar: histeresis ON < 50 lux, OFF > 400 lux
  if (luz_lux < LUZ_ENCENDER) {
    digitalWrite(PIN_LUZ, LOW);
  } else if (luz_lux > LUZ_APAGAR) {
    digitalWrite(PIN_LUZ, HIGH);
  }
}

// ============================================================
void mostrarLCD() {
  lcd.setCursor(0, 0);
  lcd.print("T:");
  lcd.print(temperatura, 1);
  lcd.print("C H:");
  lcd.print((int)humedad_ambiente);
  lcd.print("%  ");

  lcd.setCursor(0, 1);
  lcd.print("S:");
  lcd.print(humedad_suelo);
  lcd.print("% L:");
  lcd.print((int)luz_lux);
  lcd.print("   ");
}

// ============================================================
void mostrarSerial() {
  Serial.println("-----------------------------");
  Serial.print("Temperatura:      "); Serial.print(temperatura); Serial.println(" C");
  Serial.print("Humedad ambiente: "); Serial.print(humedad_ambiente); Serial.println(" %");
  Serial.print("Humedad suelo:    "); Serial.print(humedad_suelo); Serial.println(" %");
  Serial.print("Luminosidad:      "); Serial.print(luz_lux); Serial.println(" lux");
  Serial.println("--- Actuadores ---");
  Serial.print("Ventilador: "); Serial.println(temperatura > TEMP_MAX ? "ON" : "OFF");
  Serial.print("Bomba:      "); Serial.println(humedad_suelo < HUMEDAD_MIN ? "ON" : "OFF");
  Serial.print("Calefactor: "); Serial.println(temperatura < TEMP_MIN ? "ON" : "OFF");
  Serial.print("Luz:        "); Serial.println(luz_lux < LUZ_ENCENDER ? "ON" : "OFF");
  Serial.println("-----------------------------");
}