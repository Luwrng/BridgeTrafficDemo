using BridgeTrafficDemo.Models;
using BridgeTrafficDemo.Services;

var tests = new (string Name, Action Run)[]
{
    ("graph routes and conflicts", GraphRoutesAndConflicts),
    ("FSM transition guards", StateMachineGuards),
    ("demo startup is ready for route simulation", DemoStartupReadyForRoutes),
    ("empty bridge shows green for the ready direction without granting entry", EmptyBridgeReadySignals),
    ("approach stays green until D-IN and returns green after confirmed exit", SignalsFollowBridgeOccupancy),
    ("bridge lifecycle and reset guard", BridgeLifecycleAndResetGuard),
    ("concurrent route requests serialize rights", ConcurrentRequestsSerializeRights),
    ("sensor without permission faults safely", UnauthorizedSensorFaults),
    ("barrier remains manual", BarrierRemainsManual),
    ("unverified R4 is accepted in simulation", UnverifiedR4AcceptedInSimulation),
    ("R4 preserves source sequence and two bridge trips", R4KeepsSourceSequence),
    ("route step counts and final progress match configuration", RouteStepCountsAndProgress),
    ("only A and B have traffic signals", OnlyAAndBHaveSignals),
    ("queued route receives green after bridge clears", QueuedRouteGetsGreenAfterClear),
    ("selecting a route reloads its start position", SelectingRouteReloadsStart),
    ("selecting and queueing a route preserves the active vehicle", SelectingRoutePreservesActiveVehicle),
    ("recognized plate is retained in operations state", RecognizedPlateIsRetained),
    ("active plate cannot start a duplicate trip", ActivePlateCannotStartDuplicateTrip),
    ("invalid recognized plate is rejected", InvalidRecognizedPlateIsRejected)
};

var failures = new List<string>();
foreach (var test in tests)
{
    try
    {
        test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception error)
    {
        failures.Add($"FAIL {test.Name}: {error.Message}");
        Console.WriteLine(failures[^1]);
    }
}

Console.WriteLine($"{tests.Length - failures.Count}/{tests.Length} tests passed.");
return failures.Count == 0 ? 0 : 1;

static void GraphRoutesAndConflicts()
{
    Equal(0, TrafficNetwork.ValidateGraph().Count, "network graph validation");
    Equal("B → C → D → A → D → C", TrafficNetwork.FindRoute("R4")!.SourceRoute, "R4 source sequence");
    Check(!TrafficNetwork.FindRoute("R4")!.Verified, "R4 must stay unverified");
    Check(!TrafficNetwork.FindRoute("R3")!.UsesBridge, "R3 bypasses D");
    Check(TrafficNetwork.FindRoute("R5")!.UsesBridge && TrafficNetwork.FindRoute("R6")!.UsesBridge, "E branch routes cross D");
    Check(TrafficNetwork.GetConflicts("R1").Contains("R3"), "R1 and R3 share C merge");
    Check(!TrafficNetwork.GetConflicts("R5").Contains("R3"), "R3 does not share D/E zone with R5");
}

static void StateMachineGuards()
{
    var machine = new BridgeStateMachine();
    Check(machine.Phase == BridgePhases.Unknown, "initial phase is UNKNOWN");
    Check(!machine.TryReserve("R1", "V1", true, true, true, out _), "UNKNOWN cannot reserve");
    Check(machine.TryConfirmEmpty(true, out _), "explicit clear evidence permits EMPTY");
    Check(machine.TryReserve("R1", "V1", true, true, true, out _), "EMPTY can reserve");
    Check(!machine.TryReserve("R2", "V2", true, true, true, out _), "second grant rejected");
    Check(machine.TryEnter("R1", "V1", out _), "matching vehicle enters");
    Check(!machine.TryBeginClearing("R2", "V2", out _), "wrong vehicle cannot clear");
    Check(machine.Phase == BridgePhases.Fault && machine.IsOccupied, "fault retains possible occupancy");
}

static void DemoStartupReadyForRoutes()
{
    var simulation = new BridgeSimulationService();
    Equal(BridgePhases.Empty, simulation.GetState().Phase, "simulation starts with an empty bridge");
    Check(simulation.GetState().BarrierOpen, "simulation barrier starts open");
    Check(simulation.GetState().Sensors.Values.All(sensor => sensor == "CLEAR"), "simulation sensors start clear");
    var bridgeRequest = simulation.StartScenario(new StartScenarioRequest("R1"));
    Check(bridgeRequest.Ok && !bridgeRequest.Queued, "bridge route is ready without manual setup");
    Check(simulation.Step().Ok, "first simulation step reserves D");
    Equal(BridgePhases.Reserved, simulation.GetState().Phase, "first step starts bridge route");
}

static void EmptyBridgeReadySignals()
{
    var simulation = new BridgeSimulationService();
    var state = simulation.GetState();
    Equal("GREEN", state.Signals["A"], "empty startup gives R1 approach green");
    Equal("GREEN", state.Signals["B"], "both approaches ready while empty and no vehicle is active");
    Check(state.GrantedDirection is null && state.Vehicles.Count == 0, "ready indication does not create vehicles or grant D");
    Check(simulation.SelectScenario("R2").Ok, "select reverse direction");
    state = simulation.GetState();
    Equal("GREEN", state.Signals["B"], "ready green follows selected B route");
    Equal("GREEN", state.Signals["A"], "idle A remains ready when no vehicle owns D");
    Check(simulation.SetBarrier(new BarrierRequest("CLOSED")).Ok, "close barrier");
    Check(simulation.GetState().Signals.Values.All(signal => signal == "RED"), "closed barrier prevents ready green");
    Check(simulation.SetBarrier(new BarrierRequest("OPEN")).Ok, "open barrier");
    simulation.SetFaults(new FaultRequest(ExitBlocked: true));
    Check(simulation.GetState().Signals.Values.All(signal => signal == "RED"), "blocked exit prevents ready green");
    simulation.SetFaults(new FaultRequest(EmergencyStop: true));
    Check(simulation.GetState().Signals.Values.All(signal => signal == "RED"), "fault overrides an empty bridge");
    simulation.ClearFaults();
    Check(simulation.GetState().Signals.Values.All(signal => signal != "GREEN"), "UNKNOWN cannot show ready green");
}

static void SignalsFollowBridgeOccupancy()
{
    var simulation = new BridgeSimulationService();
    Check(simulation.StartScenario(new StartScenarioRequest("R1", "SIGNAL-TEST")).Ok, "start route");
    Check(simulation.Step().Ok && simulation.Step().Ok, "reserve and approach");
    var state = simulation.GetState();
    Equal(BridgePhases.Reserved, state.Phase, "approaching has not entered D");
    Equal("A", state.Vehicles.Single().CurrentNode, "last camera checkpoint stays A until entry");
    Equal("GREEN", state.Signals["A"], "authorized approach remains green");
    Check(simulation.Step().Ok, "D-IN confirms entry");
    state = simulation.GetState();
    Equal("D", state.Vehicles.Single().CurrentNode, "D-IN updates checkpoint to D");
    Check(state.Signals.Values.All(signal => signal == "RED"), "D occupancy stops both bridge approaches");
    Check(simulation.Step().Ok && simulation.Step().Ok, "cross and leave");
    Check(simulation.GetState().Signals.Values.All(signal => signal == "RED"), "CLEARING remains red until sensor confirmation");
    Check(simulation.Step().Ok, "confirm bridge empty");
    state = simulation.GetState();
    Equal(BridgePhases.Empty, state.Phase, "bridge empty after completion");
    Equal("GREEN", state.Signals["A"], "ready green returns after confirmed exit");
    Equal("GREEN", state.Signals["B"], "other approach also returns green after the trip releases D");
    Equal("C", state.Vehicles.Single().CurrentNode, "route reaches destination camera C");
}

static void BridgeLifecycleAndResetGuard()
{
    var simulation = ReadySimulation();
    Check(simulation.StartScenario(new StartScenarioRequest("R1", "CAR-1")).Ok, "accept R1");
    Check(simulation.Step().Ok, "reserve D");
    Equal(BridgePhases.Reserved, simulation.GetState().Phase, "reserved state");
    Equal("GREEN", simulation.GetState().Signals["A"], "only authorized A green");
    Check(simulation.Step().Ok, "approach");
    Check(simulation.Step().Ok, "D-IN");
    Equal(BridgePhases.Occupied, simulation.GetState().Phase, "occupied state");
    Check(simulation.GetState().Signals.Values.All(signal => signal == "RED"), "all signals red while occupied");
    Check(!simulation.Reset().Ok, "reset rejected while occupied");
    Equal(BridgePhases.Occupied, simulation.GetState().Phase, "reset preserves occupancy");
    Check(simulation.Step().Ok, "crossing");
    Check(simulation.Step().Ok, "D-OUT");
    Equal(BridgePhases.Clearing, simulation.GetState().Phase, "clearing state");
    Check(simulation.Step().Ok, "confirm sensors clear");
    Equal(BridgePhases.Empty, simulation.GetState().Phase, "only clear evidence releases D");
}

static void ConcurrentRequestsSerializeRights()
{
    var simulation = ReadySimulation();
    var routes = new[] { "R1", "R2", "R5", "R6", "R1", "R2" };
    Parallel.For(0, routes.Length, index =>
        simulation.StartScenario(new StartScenarioRequest(routes[index], $"V-{index}")));
    var state = simulation.GetState();
    Equal(1, state.Vehicles.Count(vehicle => vehicle.JourneyState == "READY"), "one active conflicting route");
    Equal(routes.Length - 1, state.Queue.Count, "other conflicting routes FIFO queued");
    var activeVehicle = state.Vehicles.Single(vehicle => vehicle.JourneyState == "READY");
    Equal(activeVehicle.Id, state.CurrentVehicleId, "display state stays bound to the active vehicle");
    Equal(activeVehicle.RouteId, state.Scenario, "display scenario stays bound to the active route");
    Parallel.Invoke(() => simulation.Step(), () => simulation.Step());
    state = simulation.GetState();
    Equal(BridgePhases.Reserved, state.Phase, "exactly one reservation under concurrent steps");
    Equal(1, state.Vehicles.Count(vehicle => vehicle.HasBridgePermission), "one vehicle owns D grant");
}

static void UnauthorizedSensorFaults()
{
    var simulation = new BridgeSimulationService();
    var result = simulation.UpdateSensor(new SensorUpdateRequest("D-IN", "DETECTED"));
    Check(!result.Ok, "unauthorized D-IN rejected");
    var state = simulation.GetState();
    Equal(BridgePhases.Fault, state.Phase, "unauthorized entry latches fault");
    Check(state.Occupied, "unauthorized entry marks possible occupancy");
    Check(state.Signals.Values.All(signal => signal == "RED"), "fault forces simulated signals red");
}

static void BarrierRemainsManual()
{
    var simulation = new BridgeSimulationService();
    Equal("OPEN", simulation.GetState().BarrierState, "simulation barrier starts open");
    Check(simulation.SetBarrier(new BarrierRequest("CLOSED", "guard-1", "manual test")).Ok, "manual close accepted");
    var blocked = simulation.StartScenario(new StartScenarioRequest("R1", "BARRIER-1"));
    Check(blocked.Ok && blocked.Queued, "closed/unknown barrier queues the vehicle");
    Check(!simulation.GetState().BarrierOpen, "barrier state reflects manual close");
    Check(simulation.SetBarrier(new BarrierRequest("OPEN", "guard-1", "manual test")).Ok, "manual open accepted");
    Check(simulation.GetState().BarrierOpen, "barrier state follows explicit manual event");
    Equal(BridgePhases.Reserved, simulation.GetState().Phase, "opening barrier reserves D for the queued vehicle");
    Equal("GREEN", simulation.GetState().Signals["A"], "queued A approach receives green after manual opening");
}

static void UnverifiedR4AcceptedInSimulation()
{
    var simulation = new BridgeSimulationService();
    var result = simulation.StartScenario(new StartScenarioRequest("R4", "TRUCK-1", "garbage"));
    Check(result.Ok && !result.Queued, "R4 starts in simulation without manual setup");
    Check(!simulation.GetState().RouteVerified, "route remains visibly unverified");
}

static void R4KeepsSourceSequence()
{
    var simulation = new BridgeSimulationService();
    Check(simulation.StartScenario(new StartScenarioRequest("R4", "GARBAGE-1", "garbage")).Ok, "simulation accepts R4");
    Check(simulation.Step().Ok, "authorize B to C");
    Equal("GREEN", simulation.GetState().Signals["B"], "B-to-C receives B signal");
    Check(simulation.Step().Ok && simulation.Step().Ok, "complete B-to-C segment");
    Check(simulation.Step().Ok, "reserve unverified C-to-D segment");
    Equal(BridgePhases.Reserved, simulation.GetState().Phase, "first D entry is separately reserved");
    Check(simulation.GetState().Signals.Values.All(signal => signal != "GREEN"), "unverified C entry has no fabricated signal");
    Check(simulation.Step().Ok && simulation.Step().Ok, "approach and enter first D traversal");
    Equal(BridgePhases.Occupied, simulation.GetState().Phase, "first D traversal occupies bridge");
    Check(simulation.Step().Ok && simulation.Step().Ok && simulation.Step().Ok, "clear first D traversal");
    Equal(BridgePhases.Reserved, simulation.GetState().Phase, "next R4 segment reserves after clear evidence");
    Equal("GREEN", simulation.GetState().Signals["A"], "A green for the next R4 segment");
    Check(simulation.Step().Ok, "reserve second traversal separately");
    Equal(BridgePhases.Reserved, simulation.GetState().Phase, "second traversal has new reservation");
    Equal("GREEN", simulation.GetState().Signals["A"], "second traversal uses A signal");
}

static void RouteStepCountsAndProgress()
{
    var expectedCounts = new Dictionary<string, int>
    {
        ["R1"] = 6, ["R2"] = 6, ["R3"] = 3,
        ["R4"] = 15, ["R5"] = 6, ["R6"] = 6
    };

    foreach (var (route, expectedCount) in expectedCounts)
    {
        var simulation = new BridgeSimulationService();
        Check(simulation.StartScenario(new StartScenarioRequest(route, $"COUNT-{route}")).Ok, $"start {route}");
        Equal(expectedCount, simulation.GetState().StepCount, $"configured {route} step count");
        for (var index = 0; index < expectedCount; index++)
            Check(simulation.Step().Ok, $"step {index + 1} for {route}");

        var state = simulation.GetState();
        Equal(route, state.Scenario, $"completed scenario {route}");
        Equal(expectedCount, state.StepIndex, $"completed {route} progress");
        Equal(expectedCount, state.StepCount, $"completed {route} total");
        Equal(1d, state.RouteProgress, $"completed {route} route progress");
        Check(state.Vehicles.Any(vehicle => vehicle.Id == $"COUNT-{route}" && vehicle.StepIndex == expectedCount), $"completed vehicle progress for {route}");
    }
}

static void OnlyAAndBHaveSignals()
{
    var simulation = new BridgeSimulationService();
    var state = simulation.GetState();
    Check(state.Signals.Keys.OrderBy(key => key).SequenceEqual(new[] { "A", "B" }), "signal map contains A/B only");
    Check(state.SignalDetails.All(signal => signal.Id is "A" or "B"), "signal details contain A/B only");
    Equal("NONE", TrafficNetwork.FindRoute("R6")!.EntrySignal, "E route has no traffic signal");

    Check(simulation.StartScenario(new StartScenarioRequest("R6", "E-NO-LIGHT")).Ok, "accept E-to-A route");
    Check(simulation.Step().Ok, "reserve E-to-A bridge passage");
    Check(simulation.GetState().Signals.Values.All(signal => signal != "GREEN"), "E approach does not fabricate a green signal");
}

static void QueuedRouteGetsGreenAfterClear()
{
    var simulation = new BridgeSimulationService();
    Check(simulation.StartScenario(new StartScenarioRequest("R1", "FIRST-A")).Ok, "start A route");
    Check(simulation.Step().Ok, "reserve A route");
    var queued = simulation.StartScenario(new StartScenarioRequest("R2", "NEXT-B"));
    Check(queued.Ok && queued.Queued, "queue B route while D is reserved");

    for (var index = 0; index < 5; index++)
        Check(simulation.Step().Ok, $"advance first route step {index + 1}");

    var state = simulation.GetState();
    Equal(BridgePhases.Reserved, state.Phase, "next route reserves when D becomes empty");
    Equal("NEXT-B", state.CurrentVehicleId, "next queued vehicle owns the active state");
    Equal("GREEN", state.Signals["B"], "B green for the queued route");
    Equal("RED", state.Signals["A"], "A remains red when B owns the grant");
}

static void SelectingRouteReloadsStart()
{
    var simulation = new BridgeSimulationService();
    Check(simulation.StartScenario(new StartScenarioRequest("R2", "DONE-R2")).Ok, "start prior route");
    for (var index = 0; index < 6; index++)
        Check(simulation.Step().Ok, $"complete prior route step {index + 1}");

    var result = simulation.SelectScenario("R1");
    Check(result.Ok, "select R1 after prior route completes");
    var state = simulation.GetState();
    Equal("R1", state.Scenario, "selected route is the backend scenario");
    Equal(0, state.StepIndex, "selection resets route progress");
    Equal(6, state.StepCount, "selected route has its configured step count");
    Check(state.CurrentVehicleId is null, "previous vehicle is removed from the map preview");
}

static void SelectingRoutePreservesActiveVehicle()
{
    var simulation = new BridgeSimulationService();
    Check(simulation.StartScenario(new StartScenarioRequest("R1", "ACTIVE-R1")).Ok, "start R1");
    Check(simulation.Step().Ok, "reserve D for active R1");

    var result = simulation.SelectScenario("R2");
    Check(result.Ok, "allow selecting a different route while a vehicle is active");
    var queued = simulation.StartScenario(new StartScenarioRequest("R2", "QUEUED-R2"));
    Check(queued.Ok && queued.Queued, "queue the newly selected route without interrupting R1");
    var state = simulation.GetState();
    Equal("R1", state.Scenario, "active R1 keeps control of its route state");
    Equal("ACTIVE-R1", state.CurrentVehicleId, "active vehicle is not teleported or discarded");
    Equal(1, state.Queue.Count, "selected trip waits in the queue");
    Equal(BridgePhases.Reserved, state.Phase, "active bridge reservation is preserved");

    for (var index = 0; index < 5; index++)
        Check(simulation.Step().Ok, $"finish active R1 step {index + 2}");
    Check(simulation.Step().Ok, "promote and reserve queued R2 after R1 completes");
    state = simulation.GetState();
    Equal("QUEUED-R2", state.CurrentVehicleId, "queued R2 becomes the active vehicle");
    Equal(BridgePhases.Reserved, state.Phase, "queued R2 receives the bridge after R1 clears");
}

static void RecognizedPlateIsRetained()
{
    var simulation = new BridgeSimulationService();
    var started = simulation.StartScenario(new StartScenarioRequest("R1", LicensePlate: "29a12345"));
    Check(started.Ok, "accept recognized vehicle");
    Equal("29A12345", simulation.GetVehicles().Single().LicensePlate, "store normalized plate on vehicle");

    var queued = simulation.StartScenario(new StartScenarioRequest("R2", LicensePlate: "30B98765"));
    Check(queued.Ok && queued.Queued, "queue another recognized vehicle");
    Equal("30B98765", simulation.GetQueue().Single().LicensePlate, "retain plate in FIFO queue");
}

static void ActivePlateCannotStartDuplicateTrip()
{
    var simulation = new BridgeSimulationService();
    var started = simulation.StartScenario(new StartScenarioRequest("R1", LicensePlate: "51B50245"));
    Check(started.Ok, "start first trip for plate");

    var duplicate = simulation.StartScenario(new StartScenarioRequest("R1", LicensePlate: "51B50245"));
    Check(!duplicate.Ok, "reject a second active trip for the same plate");
    Equal(1, simulation.GetVehicles().Count, "do not add a duplicate active vehicle");

    for (var index = 0; index < 6; index++)
        Check(simulation.Step().Ok, $"complete original trip step {index + 1}");
    Check(simulation.StartScenario(new StartScenarioRequest("R1", LicensePlate: "51B50245")).Ok,
        "allow the same plate to run again after its trip completes");
}

static void InvalidRecognizedPlateIsRejected()
{
    var simulation = new BridgeSimulationService();
    var result = simulation.StartScenario(new StartScenarioRequest("R1", LicensePlate: "not-a-plate"));
    Check(!result.Ok, "reject malformed plate");
    Equal(0, simulation.GetVehicles().Count, "do not add invalid plate to operations list");
}

static BridgeSimulationService ReadySimulation()
{
    return new BridgeSimulationService();
}

static void Check(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
}

static void Equal<T>(T expected, T actual, string message)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
        throw new InvalidOperationException($"{message}: expected {expected}, got {actual}");
}
