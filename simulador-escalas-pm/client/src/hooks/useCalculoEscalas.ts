import { DIAS_SEMANA, ESCALA_REGRAS, VALORES_HORA } from "@/../../shared/const";

interface CalculoResult {
  horasDiurnas: number;
  horasNoturnas: number;
  valorDiurno: number;
  valorNoturno: number;
  valorTotal: number;
  detalhes: string[];
}

function timeToMinutes(time: string): number {
  const [hours, minutes] = time.split(":").map(Number);
  return hours * 60 + minutes;
}

function getEscalaForDate(date: Date): string {
  const dayOfWeek = date.getDay() === 0 ? 7 : date.getDay(); // Convert Sunday from 0 to 7
  const diaInfo = DIAS_SEMANA.find((d) => d.dia === dayOfWeek);
  return diaInfo?.escala || "AZUL";
}

function calcularHorasNoTurno(
  dataInicio: Date,
  dataFim: Date,
  turno: "DIURNO" | "NOTURNO"
): number {
  const regra = ESCALA_REGRAS[turno];
  const inicioTurnoMinutos = timeToMinutes(regra.inicio);
  const fimTurnoMinutos = timeToMinutes(regra.fim);

  let totalMinutos = 0;
  let currentDate = new Date(dataInicio);

  while (currentDate <= dataFim) {
    const dayStart = new Date(currentDate);
    dayStart.setHours(0, 0, 0, 0);

    const dayEnd = new Date(currentDate);
    dayEnd.setHours(23, 59, 59, 999);

    let periodoInicio = new Date(currentDate);
    let periodoFim = new Date(currentDate);

    if (turno === "DIURNO") {
      // Turno diurno: 05:01 até 21:59
      periodoInicio.setHours(5, 1, 0, 0);
      periodoFim.setHours(21, 59, 59, 999);
    } else {
      // Turno noturno: 22:00 até 05:00 (próximo dia)
      periodoInicio.setHours(22, 0, 0, 0);
      periodoFim = new Date(currentDate);
      periodoFim.setDate(periodoFim.getDate() + 1);
      periodoFim.setHours(5, 0, 59, 999);
    }

    // Calcular intersecção
    const intersecaoInicio = new Date(Math.max(dataInicio.getTime(), periodoInicio.getTime()));
    const intersecaoFim = new Date(Math.min(dataFim.getTime(), periodoFim.getTime()));

    if (intersecaoInicio <= intersecaoFim) {
      totalMinutos += (intersecaoFim.getTime() - intersecaoInicio.getTime()) / (1000 * 60);
    }

    currentDate.setDate(currentDate.getDate() + 1);
  }

  return totalMinutos / 60; // Converter para horas
}

export function useCalculoEscalas(
  dataInicio: Date,
  dataFim: Date,
  turnoSelecionado?: "DIURNO" | "NOTURNO"
): CalculoResult {
  const detalhes: string[] = [];

  // Se um turno específico foi selecionado, calcular apenas para esse turno
  if (turnoSelecionado) {
    const escala = getEscalaForDate(dataInicio);
    const horas = calcularHorasNoTurno(dataInicio, dataFim, turnoSelecionado);
    const chave = `${escala}-${turnoSelecionado}` as keyof typeof VALORES_HORA;
    const valorHora = VALORES_HORA[chave];
    const valor = horas * valorHora;

    detalhes.push(`Escala: ${escala}`);
    detalhes.push(`Turno: ${turnoSelecionado}`);
    detalhes.push(`Horas: ${horas.toFixed(2)}`);
    detalhes.push(`Valor/hora: R$ ${valorHora.toFixed(2)}`);

    if (turnoSelecionado === "DIURNO") {
      return {
        horasDiurnas: horas,
        horasNoturnas: 0,
        valorDiurno: valor,
        valorNoturno: 0,
        valorTotal: valor,
        detalhes,
      };
    } else {
      return {
        horasDiurnas: 0,
        horasNoturnas: horas,
        valorDiurno: 0,
        valorNoturno: valor,
        valorTotal: valor,
        detalhes,
      };
    }
  }

  // Calcular para ambos os turnos
  const horasDiurnas = calcularHorasNoTurno(dataInicio, dataFim, "DIURNO");
  const horasNoturnas = calcularHorasNoTurno(dataInicio, dataFim, "NOTURNO");

  const escala = getEscalaForDate(dataInicio);

  const chaveD = `${escala}-DIURNO` as keyof typeof VALORES_HORA;
  const chavN = `${escala}-NOTURNO` as keyof typeof VALORES_HORA;

  const valorHoraDiurno = VALORES_HORA[chaveD];
  const valorHoraNoturno = VALORES_HORA[chavN];

  const valorDiurno = horasDiurnas * valorHoraDiurno;
  const valorNoturno = horasNoturnas * valorHoraNoturno;
  const valorTotal = valorDiurno + valorNoturno;

  detalhes.push(`Escala: ${escala}`);
  detalhes.push(`Horas Diurnas: ${horasDiurnas.toFixed(2)}`);
  detalhes.push(`Valor/hora Diurno: R$ ${valorHoraDiurno.toFixed(2)}`);
  detalhes.push(`Subtotal Diurno: R$ ${valorDiurno.toFixed(2)}`);
  detalhes.push(`Horas Noturnas: ${horasNoturnas.toFixed(2)}`);
  detalhes.push(`Valor/hora Noturno: R$ ${valorHoraNoturno.toFixed(2)}`);
  detalhes.push(`Subtotal Noturno: R$ ${valorNoturno.toFixed(2)}`);

  return {
    horasDiurnas,
    horasNoturnas,
    valorDiurno,
    valorNoturno,
    valorTotal,
    detalhes,
  };
}
