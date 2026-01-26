
import { Regulation } from './types';
import { REGULATIONS_PART1 } from './data_part1';
import { REGULATIONS_PART2 } from './data_part2';

export const REGULATIONS_DATA: Regulation[] = [
  ...REGULATIONS_PART1,
  ...REGULATIONS_PART2,
];
