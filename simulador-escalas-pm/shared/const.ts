export const COOKIE_NAME = "app_session_id";
export const ONE_YEAR_MS = 1000 * 60 * 60 * 24 * 365;

// Regras de escalas e valores
export const ESCALA_REGRAS = {
  DIURNO: {
    inicio: "05:01",
    fim: "21:59",
    label: "Diurno (05:01 - 21:59)",
  },
  NOTURNO: {
    inicio: "22:00",
    fim: "05:00",
    label: "Noturno (22:00 - 05:00)",
  },
};

export const VALORES_HORA = {
  "VERMELHA-DIURNO": 36.41,
  "VERMELHA-NOTURNO": 41.38,
  "AZUL-DIURNO": 26.47,
  "AZUL-NOTURNO": 29.8,
};

// Mapeamento de dias da semana para escalas
export const DIAS_SEMANA = [
  { dia: 1, nome: "Domingo", escala: "VERMELHA" },
  { dia: 2, nome: "Segunda", escala: "AZUL" },
  { dia: 3, nome: "Terça", escala: "AZUL" },
  { dia: 4, nome: "Quarta", escala: "AZUL" },
  { dia: 5, nome: "Quinta", escala: "AZUL" },
  { dia: 6, nome: "Sexta", escala: "VERMELHA" },
  { dia: 7, nome: "Sábado", escala: "VERMELHA" },
];

export const ESCALAS = ["AZUL", "VERMELHA"];
export const TURNOS = ["DIURNO", "NOTURNO"];
