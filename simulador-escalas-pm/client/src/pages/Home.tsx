import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useCalculoEscalas } from "@/hooks/useCalculoEscalas";
import { DIAS_SEMANA, VALORES_HORA } from "@/../../shared/const";
import { AlertCircle, Calculator, DollarSign } from "lucide-react";
import { useEffect, useState } from "react";

export default function Home() {
  const [dataInicio, setDataInicio] = useState<string>("");
  const [dataFim, setDataFim] = useState<string>("");
  const [turnoSelecionado, setTurnoSelecionado] = useState<"DIURNO" | "NOTURNO" | "AMBOS">("AMBOS");
  const [resultado, setResultado] = useState<any>(null);
  const [erro, setErro] = useState<string>("");

  // Set today's date as default
  useEffect(() => {
    const hoje = new Date().toISOString().split("T")[0];
    setDataInicio(hoje);
    setDataFim(hoje);
  }, []);

  const handleCalcular = () => {
    setErro("");

    if (!dataInicio || !dataFim) {
      setErro("Por favor, preencha as datas de início e fim.");
      return;
    }

    const inicio = new Date(dataInicio + "T00:00:00");
    const fim = new Date(dataFim + "T23:59:59");

    if (inicio > fim) {
      setErro("A data de início não pode ser maior que a data de fim.");
      return;
    }

    const turno = turnoSelecionado === "AMBOS" ? undefined : turnoSelecionado;
    const calc = useCalculoEscalas(inicio, fim, turno);
    setResultado(calc);
  };

  const handleLimpar = () => {
    const hoje = new Date().toISOString().split("T")[0];
    setDataInicio(hoje);
    setDataFim(hoje);
    setTurnoSelecionado("AMBOS");
    setResultado(null);
    setErro("");
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      {/* Header */}
      <header className="border-b border-slate-200 bg-white shadow-sm">
        <div className="container mx-auto px-4 py-6">
          <div className="flex items-center gap-3">
            <Calculator className="h-8 w-8 text-blue-600" />
            <div>
              <h1 className="text-3xl font-bold text-slate-900">Simulador de Escalas PM</h1>
              <p className="text-sm text-slate-600">Cálculo de serviços extras remunerados</p>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-4 py-8">
        <div className="grid gap-8 lg:grid-cols-3">
          {/* Form Section */}
          <div className="lg:col-span-1">
            <Card className="sticky top-4 shadow-lg">
              <CardHeader className="bg-gradient-to-r from-blue-50 to-blue-100 border-b">
                <CardTitle className="text-xl">Simulador</CardTitle>
                <CardDescription>Preencha os dados para calcular</CardDescription>
              </CardHeader>
              <CardContent className="pt-6 space-y-6">
                {/* Data Início */}
                <div className="space-y-2">
                  <Label htmlFor="data-inicio" className="font-semibold text-slate-700">
                    Data de Início
                  </Label>
                  <Input
                    id="data-inicio"
                    type="date"
                    value={dataInicio}
                    onChange={(e) => setDataInicio(e.target.value)}
                    className="border-slate-300"
                  />
                </div>

                {/* Data Fim */}
                <div className="space-y-2">
                  <Label htmlFor="data-fim" className="font-semibold text-slate-700">
                    Data de Fim
                  </Label>
                  <Input
                    id="data-fim"
                    type="date"
                    value={dataFim}
                    onChange={(e) => setDataFim(e.target.value)}
                    className="border-slate-300"
                  />
                </div>

                {/* Turno */}
                <div className="space-y-2">
                  <Label htmlFor="turno" className="font-semibold text-slate-700">
                    Turno
                  </Label>
                  <Select value={turnoSelecionado} onValueChange={(value: any) => setTurnoSelecionado(value)}>
                    <SelectTrigger id="turno" className="border-slate-300">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="AMBOS">Ambos os Turnos</SelectItem>
                      <SelectItem value="DIURNO">Apenas Diurno</SelectItem>
                      <SelectItem value="NOTURNO">Apenas Noturno</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Error Message */}
                {erro && (
                  <div className="flex gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                    <AlertCircle className="h-5 w-5 flex-shrink-0 mt-0.5" />
                    <span>{erro}</span>
                  </div>
                )}

                {/* Buttons */}
                <div className="flex gap-2 pt-4">
                  <Button onClick={handleCalcular} className="flex-1 bg-blue-600 hover:bg-blue-700">
                    <Calculator className="mr-2 h-4 w-4" />
                    Calcular
                  </Button>
                  <Button onClick={handleLimpar} variant="outline" className="flex-1">
                    Limpar
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Results Section */}
          <div className="lg:col-span-2 space-y-6">
            {resultado ? (
              <>
                {/* Main Result Card */}
                <Card className="shadow-lg border-0 bg-gradient-to-br from-green-50 to-emerald-50">
                  <CardHeader className="bg-gradient-to-r from-green-500 to-emerald-600 text-white rounded-t-lg">
                    <CardTitle className="flex items-center gap-2">
                      <DollarSign className="h-6 w-6" />
                      Valor Total
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="pt-8">
                    <div className="text-center">
                      <p className="text-5xl font-bold text-green-600 mb-2">
                        R$ {resultado.valorTotal.toFixed(2)}
                      </p>
                      <p className="text-slate-600 text-sm">Valor total de serviços extras remunerados</p>
                    </div>
                  </CardContent>
                </Card>

                {/* Breakdown Cards */}
                <div className="grid gap-4 sm:grid-cols-2">
                  {/* Diurno */}
                  <Card className="shadow-md border-l-4 border-l-amber-500">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-lg text-amber-700">Turno Diurno</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                      <div className="flex justify-between text-sm">
                        <span className="text-slate-600">Horas:</span>
                        <span className="font-semibold">{resultado.horasDiurnas.toFixed(2)}h</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-slate-600">Valor:</span>
                        <span className="font-semibold text-amber-600">R$ {resultado.valorDiurno.toFixed(2)}</span>
                      </div>
                    </CardContent>
                  </Card>

                  {/* Noturno */}
                  <Card className="shadow-md border-l-4 border-l-indigo-500">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-lg text-indigo-700">Turno Noturno</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                      <div className="flex justify-between text-sm">
                        <span className="text-slate-600">Horas:</span>
                        <span className="font-semibold">{resultado.horasNoturnas.toFixed(2)}h</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-slate-600">Valor:</span>
                        <span className="font-semibold text-indigo-600">R$ {resultado.valorNoturno.toFixed(2)}</span>
                      </div>
                    </CardContent>
                  </Card>
                </div>

                {/* Details */}
                <Card className="shadow-md">
                  <CardHeader>
                    <CardTitle className="text-lg">Detalhes do Cálculo</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2 text-sm">
                      {resultado.detalhes.map((detalhe: string, idx: number) => (
                        <div key={idx} className="flex justify-between py-1 border-b border-slate-100 last:border-0">
                          <span className="text-slate-600">{detalhe.split(":")[0]}:</span>
                          <span className="font-semibold text-slate-900">{detalhe.split(":")[1]}</span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </>
            ) : (
              <Card className="shadow-lg border-dashed border-2">
                <CardContent className="pt-12 pb-12 text-center">
                  <Calculator className="h-16 w-16 text-slate-300 mx-auto mb-4" />
                  <p className="text-slate-500 text-lg">Preencha os dados e clique em "Calcular"</p>
                </CardContent>
              </Card>
            )}

            {/* Reference Table */}
            <Card className="shadow-md">
              <CardHeader className="bg-slate-50 border-b">
                <CardTitle className="text-lg">Tabela de Referência</CardTitle>
                <CardDescription>Valores por hora conforme escala e turno</CardDescription>
              </CardHeader>
              <CardContent className="pt-6">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-100 border-b-2 border-slate-300">
                      <tr>
                        <th className="px-4 py-2 text-left font-semibold text-slate-700">Escala</th>
                        <th className="px-4 py-2 text-left font-semibold text-slate-700">Turno</th>
                        <th className="px-4 py-2 text-right font-semibold text-slate-700">Valor/Hora</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(VALORES_HORA).map(([chave, valor]) => {
                        const [escala, turno] = chave.split("-");
                        return (
                          <tr key={chave} className="border-b border-slate-200 hover:bg-slate-50">
                            <td className="px-4 py-3">
                              <span className={`px-3 py-1 rounded-full text-xs font-semibold ${
                                escala === "VERMELHA"
                                  ? "bg-red-100 text-red-700"
                                  : "bg-blue-100 text-blue-700"
                              }`}>
                                {escala}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-slate-700">
                              {turno === "DIURNO" ? "🌅 Diurno" : "🌙 Noturno"}
                            </td>
                            <td className="px-4 py-3 text-right font-semibold text-green-600">
                              R$ {valor.toFixed(2)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Days Reference */}
                <div className="mt-6 pt-6 border-t">
                  <h4 className="font-semibold text-slate-700 mb-3">Escalas por Dia da Semana</h4>
                  <div className="grid gap-2 text-sm">
                    {DIAS_SEMANA.map((dia) => (
                      <div key={dia.dia} className="flex justify-between items-center py-1">
                        <span className="text-slate-600">{dia.nome}</span>
                        <span className={`px-3 py-1 rounded-full text-xs font-semibold ${
                          dia.escala === "VERMELHA"
                            ? "bg-red-100 text-red-700"
                            : "bg-blue-100 text-blue-700"
                        }`}>
                          {dia.escala}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-200 bg-white mt-12">
        <div className="container mx-auto px-4 py-6 text-center text-sm text-slate-600">
          <p>Simulador de Escalas PM © 2026 - Cálculo de Serviços Extras Remunerados</p>
        </div>
      </footer>
    </div>
  );
}
