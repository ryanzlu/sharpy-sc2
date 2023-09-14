from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit
from sc2.units import Units
from sharpy.interfaces import ILostUnitsManager, IIncomeCalculator
from .act_base import ActBase

REACTORS = {UnitTypeId.BARRACKSREACTOR, UnitTypeId.FACTORYREACTOR, UnitTypeId.STARPORTREACTOR, UnitTypeId.REACTOR}


class ActUnit(ActBase):
    """Builds units."""

    lost_units_manager: ILostUnitsManager
    income_calculator: IIncomeCalculator

    def __init__(self, unit_type: UnitTypeId, from_building: UnitTypeId, to_count: int = 9999, priority: bool = False,
                 allow_queue=True):
        assert unit_type is not None and isinstance(unit_type, UnitTypeId)
        assert from_building is not None and isinstance(from_building, UnitTypeId)
        assert to_count is not None and isinstance(to_count, int)
        assert isinstance(priority, bool)

        self.unit_type = unit_type
        self.from_building = from_building
        self.to_count = to_count
        self.priority = priority
        self.allow_queue = allow_queue

        super().__init__()

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)
        self.income_calculator = self.knowledge.get_required_manager(IIncomeCalculator)

    @property
    def builders(self) -> Units:
        """Returns available builder structures."""
        building: Unit
        _builders = self.cache.own(self.from_building).copy()
        if self.from_building is UnitTypeId.COMMANDCENTER:
            if self.cache.own(UnitTypeId.ORBITALCOMMAND).exists:
                for building in self.cache.own(UnitTypeId.ORBITALCOMMAND):
                    _builders.append(building)
                for building in self.cache.own(UnitTypeId.PLANETARYFORTRESS):
                    _builders.append(building)
        if self.from_building is UnitTypeId.HATCHERY:
            if self.cache.own(UnitTypeId.LAIR).exists:
                for building in self.cache.own(UnitTypeId.LAIR):
                    _builders.append(building)
            if self.cache.own(UnitTypeId.HIVE).exists:
                for building in self.cache.own(UnitTypeId.HIVE):
                    _builders.append(building)

        return _builders

    def get_unit_count(self) -> int:
        count = 0

        for unit in self.ai.units:
            if self.knowledge.unit_values.real_type(unit.type_id) == self.unit_type:
                count += 1

        if self.unit_type == self.knowledge.my_worker_type:
            count = max(count, self.ai.supply_workers)

        ability = self.ai._game_data.units[self.unit_type.value].creation_ability

        if self.knowledge.my_race == Race.Zerg:
            pending = sum([o.ability.id == ability.id for u in self.cache.own(UnitTypeId.EGG) for o in u.orders])
            if self.unit_type == UnitTypeId.ZERGLING:
                count += pending * 2
            else:
                count += pending

        if self.unit_type == self.knowledge.my_worker_type:
            count = max(self.ai.supply_workers, count)

        count += sum([o.ability and o.ability.id == ability.id for u in self.builders for o in u.orders])

        return count

    @property
    def is_done(self) -> bool:
        unit_count = self.get_unit_count()
        return unit_count >= self.to_count

    async def execute(self) -> bool:
        if self.is_done:
            return True

        unit_data = self.ai._game_data.units[self.unit_type.value]
        cost = self.ai._game_data.calculate_ability_cost(unit_data.creation_ability)

        if self.builders.ready.exists and self.knowledge.can_afford(unit_data.creation_ability):
            for builder in self.builders.ready:
                if self.has_order_ready(builder) and not builder.is_flying:
                    if builder.tag in self.ai.unit_tags_received_action:
                        # Skip to next builder
                        continue

                    if self.knowledge.cooldown_manager.is_ready(builder.tag, unit_data.creation_ability.id):
                        if builder.train(self.unit_type):
                            pos_formatted = f"({builder.position.x:.1f}, {builder.position.y:.1f})"
                            self.print(f"{self.unit_type.name} from {self.from_building.name} at {pos_formatted}")
                            return False  # Only one at a time

            if self.priority:
                # No available builders, reserve cost to avoid starving priority task.
                self.knowledge.reserve(cost.minerals, cost.vespene)

        elif self.priority:
            unit_data = self.ai._game_data.units[self.unit_type.value]

            if self.builders.not_ready.exists:
                cost = self.ai._game_data.calculate_ability_cost(unit_data.creation_ability)
                mineral_income = self.income_calculator.mineral_income
                gas_income = self.income_calculator.gas_income

                if mineral_income > 0:
                    m_time = cost.minerals / mineral_income
                else:
                    m_time = 0

                if cost.vespene > 0 and gas_income > 0:
                    g_time = cost.vespene / gas_income
                else:
                    g_time = 0

                time_wait = min(m_time, g_time)
                until_ready = self.building_progress(self.from_building)
                if time_wait >= until_ready:
                    self.knowledge.reserve(cost.minerals, cost.vespene)
            else:
                if self.builders.idle:
                    # TODO: Start reserving resources while building previous one
                    self.knowledge.reserve(cost.minerals, cost.vespene)
        return False

    def has_order_ready(self, builder: Unit) -> bool:
        if builder.add_on_tag != 0:
            add_on = self.cache.by_tag(builder.add_on_tag)
            has_reactor = add_on.type_id in REACTORS
        else:
            has_reactor = False

        progress_threshold_to_queue = 0.9
        if has_reactor:
            if len(builder.orders) < 2:
                return True
            if self.allow_queue:
                return builder.orders[0].progress > progress_threshold_to_queue or \
                    builder.orders[1].progress > progress_threshold_to_queue

        if self.allow_queue and len(builder.orders) == 1:
            return builder.orders[0].progress > progress_threshold_to_queue

        return len(builder.orders) == 0
